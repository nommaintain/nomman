# nomman/services/core.py
"""
Domain Services for NOMMAN.
This module implements the core business logic for nomenclature classification,
impact analysis, and the overall orchestration of the workflow.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace

from nomman.exceptions import AuthorityInitError, LabInventoryError
from nomman.models import (
    Authority,
    ClassifyReport,
    Domain,
    DomainImpact,
    DomainMembership,
    LabInventory,
    LabSystem,
    TaxonClassification,
)
from nomman.repositories import AuthorityRepo, DomainRepo, LabRepo, SystemRepo
from nomman.taxon_interface import FuzzyConfirmation, NomenMatcher
from nomman.values import FuzzyMatch, NomenStatus, TaxonName, ValidationResult

from .fuzzy import FuzzyPrevalidation
from .normalize import TaxonNormalizer, TaxonSplitter


class NomenclatureService:
    """Service for coordinating matching of taxon names against nomenclature authorities based on priority ranks."""

    def classify(
        self, names: Iterable[TaxonName], matchers: dict[str, NomenMatcher], authorities: frozenset[Authority]
    ) -> dict[TaxonName, ValidationResult]:
        """
        Performs priority-based classification.
        Names matched by a higher-priority authority are not passed to lower-priority ones.
        """
        results: dict[TaxonName, ValidationResult] = {}
        unmatched_pool = set(names)
        # Sort authorities by rank (ascending: smaller number = higher priority)
        for auth in sorted(authorities, key=lambda a: a.rank):
            if not (matcher := matchers.get(auth.name)):
                raise AuthorityInitError(f"Matcher for authority '{auth.name}' not found.")
            # Process only those names not yet matched by a higher authority
            still_unmatched: set[TaxonName] = set()
            for name in unmatched_pool:
                if (res := matcher.match(name)).status != NomenStatus.UNMATCHED:
                    results[name] = res
                else:
                    still_unmatched.add(name)
            unmatched_pool = still_unmatched
        # Any name remaining in the pool after all authorities have run is globally unmatched
        results |= {name: ValidationResult.unmatched(name) for name in unmatched_pool}
        return results


class ImpactAnalysisService:
    """Service for determining clinical impact of a name replacement from changes in domain memberships."""

    def __init__(self) -> None:
        self._membership_index: dict[TaxonName, set[DomainMembership]] = {}
        self._indexed_domains: frozenset[Domain] | None = None

    def _build_index(self, domains: frozenset[Domain]) -> None:
        index = defaultdict(set)
        for d in domains:
            for g in d.groups:
                membership = DomainMembership(domain=d, group=g)
                for member in g.members:
                    index[member].add(membership)
        self._membership_index = dict(index)
        self._indexed_domains = domains

    def analyze(self, validation: ValidationResult, domains: frozenset[Domain]) -> DomainImpact:
        """Calculates which domain memberships are lost or gained if a taxon name is replaced."""
        if self._indexed_domains != domains:  # Update index if domains changed since last call
            self._build_index(domains)
        if validation.status in (NomenStatus.VALID, NomenStatus.UNMATCHED) or validation.replacement is None:
            return DomainImpact()  # No impact if name is already valid, unmatched, or has no replacement
        return DomainImpact.from_memberships(
            orig=self._membership_index.get(validation.original_name, set()),
            repl=self._membership_index.get(validation.replacement, set()),
        )


class FuzzyPrevalCoordinator:
    """Coordinates FuzzyPrevalidation-related workflow: data gathering -> suggestion -> user confirmation."""

    def __init__(self, lab_repo: LabRepo, fuzzy: FuzzyPrevalidation, confirm: FuzzyConfirmation) -> None:
        self._lab, self._fuzzy, self._confirm = lab_repo, fuzzy, confirm

    def resolve_replacements(
        self, inventory: LabInventory, auths: frozenset[Authority], user_w: int | None = None
    ) -> tuple[dict[TaxonName, TaxonName], dict[TaxonName, tuple[FuzzyMatch, ...]]]:
        """Executes pre-validation loop and returns replacements map + original suggestions."""
        replaces: dict[TaxonName, TaxonName] = {}
        suggests = self._fuzzy.get_suggestions(inventory.get_all_names(), self._fuzzy.get_auths_names(auths), user_w)
        if suggests and self._confirm.confirm_replace(suggests):
            replaces = {orig: TaxonName(match[0].suggestion) for orig, match in suggests.items()}
        return replaces, suggests


class ClassifyOrchestrator:
    """Orchestrates Infrastructure and Domain Services for final ClassificationReport."""

    def __init__(
        self, auth_repo: AuthorityRepo, domain_repo: DomainRepo, lab_repo: LabRepo, sys_repo: SystemRepo
    ) -> None:
        self._auth_repo = auth_repo
        self._domain_repo = domain_repo
        self._lab_repo = lab_repo
        self._sys_repo = sys_repo
        self._nomen_service = NomenclatureService()
        self._impact_service = ImpactAnalysisService()
        self._normalizer = TaxonNormalizer()
        self._splitter = TaxonSplitter()

    def run_classification(
        self,
        inventory: LabInventory,
        domains_path: str,
        authorities: frozenset[Authority],
        replacements: dict[TaxonName, TaxonName] | None = None,
        suggestions: dict[TaxonName, tuple[FuzzyMatch, ...]] | None = None,
    ) -> ClassifyReport:
        """Executes NOMMAN workflow from data loading to result aggregation."""
        # Infrastructure: gather necessary data
        run_info = self._sys_repo.get_run_info()
        domains = self._domain_repo.load_domains(domains_path)
        if replacements:  # user agreed to update lab names with best fuzzy matches
            inventory = self._apply_replacements(inventory, replacements)
        # Extract matchers from active capabilities
        matchers = {auth.name: auth.matcher for auth in authorities if auth.matcher is not None}
        self._validate_aliases(inventory, matchers)
        # Domain Service: classify nomenclature
        unique_names = inventory.get_all_names()
        validations = self._nomen_service.classify(unique_names, matchers, authorities)
        # Domain Service: analyze impact and aggregate results
        classifications: list[TaxonClassification] = []
        for name in unique_names:
            v = validations[name]
            if suggestions and name in suggestions:
                v = replace(v, suggestions=suggestions[name])
            classifications.append(
                TaxonClassification(
                    taxon=name,
                    validation=v,
                    impact=self._impact_service.analyze(v, domains),
                    report_sys=inventory.get_systems_for_name(name),
                )
            )
        return ClassifyReport(
            run_info=run_info,
            authorities=authorities,
            results=frozenset(classifications),
        )

    def normalize_inventory(self, inventory: LabInventory) -> LabInventory:
        """Applies normalization & splitting to inventory names; called before matching/ classification."""
        new_systems = []
        for s in inventory.systems:
            expanded_names: set[TaxonName] = set()
            for n_obj in s.reported_names:
                # Step 1: Normalize
                norm_res = self._normalizer.normalize(n_obj)
                target_taxon = norm_res.name if norm_res.is_modified else n_obj
                # Step 2: Split (Expansion)
                expanded_names.update(self._splitter.split(target_taxon))
            new_systems.append(LabSystem(name=s.name, reported_names=frozenset(expanded_names)))
        TaxonNormalizer.normalize.cache_clear()
        TaxonSplitter.split.cache_clear()
        return LabInventory(systems=frozenset(new_systems))

    @staticmethod
    def _apply_replacements(inventory: LabInventory, replacements: dict[TaxonName, TaxonName]) -> LabInventory:
        new_systems = []
        for s in inventory.systems:
            new_reported_names = set()
            for n_obj in s.reported_names:
                if n_obj in replacements:
                    r = replacements[n_obj]
                    merged_aliases = r.aliases | {f"{n_obj.value} (replaced)"}  # Merge original value into aliases
                    new_reported_names.add(TaxonName(r.value, merged_aliases))
                else:
                    new_reported_names.add(n_obj)
            new_systems.append(LabSystem(name=s.name, reported_names=frozenset(new_reported_names)))
        return LabInventory(systems=frozenset(new_systems))

    @staticmethod
    def _validate_aliases(inventory: LabInventory, matchers: dict[str, NomenMatcher]) -> None:
        """Checks for collisions between aliases and entries in any active authority."""
        collisions: list[str] = []
        for taxon in inventory.get_all_names():
            if not taxon.aliases:
                continue
            for alias_str in taxon.aliases:
                # Wrap alias string in TaxonName with empty aliases for matcher
                alias_taxon = TaxonName(value=alias_str, aliases=frozenset())
                for auth_name, matcher in matchers.items():
                    if matcher.match(alias_taxon).status != NomenStatus.UNMATCHED:  # status not UNMATCHED = matched
                        collisions.append(
                            f"- alias '{alias_str}' (for '{taxon.value}') match entry in authority '{auth_name}'"
                        )
        if collisions:
            raise LabInventoryError(
                f"Alias collisions with entries in nomenclature authorities:\n{'\n'.join(collisions)}"
            )
