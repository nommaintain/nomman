# models.py
"""
Domain Models for NOMMAN.
This module defines the aggregate Value Objects used across the application.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

from nomman.taxon_interface import NomenMatcher, TaxonGateway
from nomman.values import TaxonName, TaxonRank, ValidationResult


@dataclass(frozen=True, slots=True)
class TaxaGroup:
    """A group of taxa, which could be a natural taxon (Genus/Species) or a user-defined custom group."""

    name: str
    rank: TaxonRank
    members: frozenset[TaxonName]


@dataclass(frozen=True, slots=True)
class Domain:
    """A domain represents a clinical or regulatory grouping (e.g., 'CLSI M100')."""

    title: str
    groups: frozenset[TaxaGroup]

    def contains_taxon(self, name: TaxonName) -> bool:
        """Checks if a specific taxon is a member of any group within this domain."""
        return any(name in group.members for group in self.groups)


@dataclass(frozen=True, slots=True)
class DomainMembership:
    """Represents the association of a taxon to a specific group within a domain."""

    domain: Domain
    group: TaxaGroup


@dataclass(frozen=True, slots=True)
class DomainImpact:
    """Represents the change in domain membership resulting from a name replacement."""

    lost: frozenset[DomainMembership] = field(default_factory=frozenset)
    gained: frozenset[DomainMembership] = field(default_factory=frozenset)

    @classmethod
    def from_memberships(cls, orig: Iterable[DomainMembership], repl: Iterable[DomainMembership]) -> "DomainImpact":
        """Calculates the difference between two membership sets to determine impact."""
        orig_set, repl_set = set(orig), set(repl)
        return cls(lost=frozenset(orig_set - repl_set), gained=frozenset(repl_set - orig_set))

    @property
    def has_impact(self) -> bool:
        """True if the name replacement changes domain membership in any way."""
        return bool(self.lost or self.gained)


@dataclass(frozen=True, slots=True)
class LabSystem:
    """Represents a source of organism names (e.g., 'Instrument A', 'LIS Y')."""

    name: str
    reported_names: frozenset[TaxonName]


@dataclass(frozen=True, slots=True)
class LabInventory:
    """Aggregate root for laboratory data. Maps the relationship between systems and the names they report."""

    systems: frozenset[LabSystem]

    def get_all_names(self) -> frozenset[TaxonName]:
        """Returns every TaxonName reported across systems."""
        all_names = set()
        for system in self.systems:
            all_names.update(system.reported_names)
        return frozenset(all_names)

    def get_systems_for_name(self, name: TaxonName) -> frozenset[LabSystem]:
        """Returns the LabSystem objects that reported the given taxon."""
        return frozenset(system for system in self.systems if name in system.reported_names)


@dataclass(frozen=True, slots=True)
class Authority:
    """
    Definition of a nomenclature authority (e.g., LPSN, Assert List).
    Encapsulates metadata along with associated validation and expansion components.
    """

    name: str
    rank: int
    source_file: str
    checksum: str
    summary: str
    matcher: NomenMatcher | None = None
    gateway: TaxonGateway | None = None


@dataclass(frozen=True, slots=True)
class TaxonClassification:
    """
    Aggregate result of the nomenclature classification process for a single taxon.
    Primary object required by the presentation layer.
    """

    taxon: TaxonName
    validation: ValidationResult
    impact: DomainImpact
    report_sys: frozenset[LabSystem]

    @property
    def is_adopted(self) -> bool:
        """Proxy to check if the taxon is considered correct/appropriate."""
        return self.validation.is_adopted

    @property
    def replacement_name(self) -> TaxonName | None:
        """Proxy to retrieve the suggested replacement name."""
        return self.validation.replacement


@dataclass(frozen=True, slots=True)
class RunInfo:
    """Metadata regarding the execution environment and invocation."""

    command_line: str
    timestamp: str
    platform: str
    python_version: str


@dataclass(frozen=True, slots=True)
class ClassifyReport:
    """Aggregate root for results of NOMMAN run to render in any output format."""

    run_info: RunInfo
    authorities: frozenset[Authority]
    results: frozenset[TaxonClassification]
