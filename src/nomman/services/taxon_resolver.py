# nomman/services/taxon_resolver.py
"""
Domain Service for NOMMAN.
Module handling resolution of raw strings into taxonomic groups.
"""

import logging
from types import MappingProxyType

from nomman.exceptions import TaxonAmbiguityError, TaxonRankError, TaxonResolutionError
from nomman.models import TaxaGroup
from nomman.taxon_interface import TaxonGateway
from nomman.values import TaxonName, TaxonRank

logger = logging.getLogger(__name__)
HIGHER_TAXON_MARKER = "Higher taxon"


class TaxonResolver:
    """Service responsible for taxonomic resolution of domain entries."""

    _RANK_MAPPING = MappingProxyType(
        {
            "DOMAIN": TaxonRank.DOMAIN,
            "KINGDOM": TaxonRank.KINGDOM,
            "PHYLUM": TaxonRank.PHYLUM,
            "CLASS": TaxonRank.CLASS,
            "SUBCLASS": TaxonRank.CLASS,
            "ORDER": TaxonRank.ORDER,
            "SUBORDER": TaxonRank.ORDER,
            "FAMILY": TaxonRank.FAMILY,
            "TRIBE": TaxonRank.FAMILY,
            "GENUS": TaxonRank.GENUS,
            "SUBGENUS": TaxonRank.GENUS,
            "SPECIES": TaxonRank.SPECIES,
            "SUBSPECIES": TaxonRank.SUBSPECIES,
        }
    )

    def __init__(self, symbol_map: dict[str, TaxonGateway]) -> None:
        self._symbol_map = symbol_map

    def resolve_entry(self, entry: str) -> TaxaGroup:
        """Routes a raw domain entry to the appropriate resolution handler."""
        if "||" in entry:
            return self._handle_custom_group(entry)
        for symbol, gateway in self._symbol_map.items():
            if entry.endswith(symbol):
                return self._handle_higher_taxon(entry[:-1].strip(), gateway)
        return self._handle_natural_taxon(entry)

    def _handle_custom_group(self, entry: str) -> TaxaGroup:
        """Resolves 'Name || Member, Member' syntax."""
        if len(parts := [p.strip() for p in entry.split("||")]) != 2 or not all(parts):
            raise TaxonResolutionError(f"Invalid custom group format in domains: {entry}")
        group_name, members_raw = parts
        validated_members = []
        for m in [m.strip() for m in members_raw.split(",") if m.strip()]:
            if not any(g.exists(m) for g in self._symbol_map.values()):
                raise TaxonResolutionError(f"Unknown taxon in custom group for domains: {m}")
            validated_members.append(TaxonName(m))
        return TaxaGroup(
            name=group_name,
            rank=TaxonRank.INVALID,  # custom groups do not have a biological rank
            members=frozenset(validated_members),
        )

    def _handle_higher_taxon(self, name_str: str, gateway: TaxonGateway) -> TaxaGroup:
        """Resolves taxa above genus level."""
        if len(name_str.split()) != 1:
            raise TaxonResolutionError(f"Name input to higher taxon resolution should be monomial: {name_str}")
        if not (result := gateway.taxon_query(name_str)):
            raise TaxonResolutionError(f"Taxon '{name_str}' could not be resolved by the {gateway.name} gateway.")
        rank_str, genera_list = result
        if rank_str == HIGHER_TAXON_MARKER:
            raise TaxonRankError("Overly broad taxa not acceptable for domains")
        if (rank := self._map_rank(rank_str)) == TaxonRank.INVALID:
            raise TaxonRankError(f"Unknown higher taxon rank returned for: {name_str}")
        if rank in (TaxonRank.DOMAIN, TaxonRank.KINGDOM, TaxonRank.PHYLUM, TaxonRank.CLASS):
            raise TaxonRankError(f"Taxa at class or above not acceptable for domains: {name_str}")
        all_members = []
        for genus in genera_list:  # Resolve all species/subspecies from the returned genera.
            if not (members := self._get_members_from_all_gateways(genus)):
                logger.warning(f"Genus '{genus}' found via gateway but no members found in databases.")
            else:
                all_members.extend(members)
        return TaxaGroup(name=name_str, rank=rank, members=frozenset(TaxonName(m) for m in all_members))

    def _handle_natural_taxon(self, entry: str) -> TaxaGroup:
        """Resolves species, subspecies, and genus based on word counts."""
        if not any(g.exists(entry) for g in self._symbol_map.values()):
            raise TaxonResolutionError(f"Unknown taxon in domains: {entry}")
        match len(epithets := entry.split()):  # word count
            case 4 if epithets[2] in {"subsp.", "var."}:  # checks for infraspecific markers
                return TaxaGroup(name=entry, rank=TaxonRank.SUBSPECIES, members=frozenset([TaxonName(entry)]))
            case 2:  # Species: "Genus species"
                return TaxaGroup(name=entry, rank=TaxonRank.SPECIES, members=frozenset([TaxonName(entry)]))
            case 1:  # Genus: "Genus"
                members = [entry, *self._get_members_from_all_gateways(entry)]
                return TaxaGroup(name=entry, rank=TaxonRank.GENUS, members=frozenset(TaxonName(m) for m in members))
        raise TaxonResolutionError(f"Unsupported taxon format in domains: {entry}")

    @staticmethod
    def _map_rank(rank_str: str) -> TaxonRank:
        """Maps authority rank strings to internal TaxonRank enums."""
        return TaxonResolver._RANK_MAPPING.get(rank_str.upper(), TaxonRank.INVALID)

    def _get_members_from_all_gateways(self, genus: str) -> list[str]:
        """Polls all gateways to retrieve members; enforces single-gateway rule to prevent taxonomic ambiguity."""
        matching_results = [(gw, m) for gw in self._symbol_map.values() if (m := gw.get_genus_members(genus))]
        match len(matching_results):
            case 0:
                return []
            case 1:  # only one gateway provided members
                return matching_results[0][1]
            case _:  # ambiguity: The genus name exists in multiple kingdoms/gateways
                gateway_names = [gw.name for gw, _ in matching_results]
                raise TaxonAmbiguityError(f"Taxonomic ambiguity: '{genus}' is defined in >1 gateways: {gateway_names}.")
