# taxon_interface.py
"""Interfaces for NOMMAN; defines the protocols for external (online) system gateways."""

from typing import Protocol

from nomman.values import FuzzyMatch, TaxonName, ValidationResult


class TaxonGateway(Protocol):
    """
    Gateway interface for taxonomic validation and resolution by LPSN data.
    Decouples the domain logic from specific API or Database implementations.
    """

    @property
    def name(self) -> str: ...

    def exists(self, name: str) -> bool:
        """Checks if a taxonomic name exists in the authority database."""
        ...

    def taxon_query(self, name: str) -> tuple[str, list[str]] | None:
        """
        Queries higher taxa.
        Returns a tuple of (Rank String, List of Genera) or None if not found.
        """
        ...

    def get_genus_members(self, genus: str) -> list[str]:
        """Returns a list of all species/subspecies members of a given genus."""
        ...


class NomenMatcher(Protocol):
    """Contract for providing nomenclature-related functionality."""

    def match(self, name: TaxonName) -> ValidationResult:
        """Matches a taxon name against the authority and returns a validation result."""
        ...

    def get_all_names(self) -> frozenset[str]:
        """Returns all taxonomic names recognized by this provider."""
        ...


class FuzzyConfirmation(Protocol):
    """Protocol for confirming fuzzy match suggestions with the user."""

    def confirm_replace(self, suggestions: dict[TaxonName, tuple[FuzzyMatch, ...]]) -> bool:
        """Returns True if the user accepts suggested replacements."""
        ...
