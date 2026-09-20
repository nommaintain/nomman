# values.py
"""
Value Objects for NOMMAN.
This module defines the atomic Value Objects used across the application.
"""

from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import NamedTuple, Self


class ReportFormat(StrEnum):
    """Supported output formats for classification reports."""

    MARKDOWN = "md"
    HTML = "html"
    CSV = "csv"


class TaxonRank(StrEnum):
    """Standardized taxonomic ranks."""

    DOMAIN = auto()
    KINGDOM = auto()
    PHYLUM = auto()
    CLASS = auto()
    ORDER = auto()
    FAMILY = auto()
    GENUS = auto()
    SPECIES = auto()
    SUBSPECIES = auto()
    INVALID = auto()


class NomenStatus(StrEnum):
    """Represents distinct statuses of a name relative to the authorities."""

    VALID = auto()  # Correct according to authorities
    INVALID_WITH_REPLACEMENT = auto()  # Incorrect, but a replacement is available
    INVALID_NO_REPLACEMENT = auto()  # Incorrect, no replacement available
    UNMATCHED = auto()  # Not found in any authority


class FuzzyMatch(NamedTuple):
    """Represents a fuzzy match and its similarity score."""

    suggestion: str
    score: float


@dataclass(frozen=True, slots=True)
class TaxonName:
    """Represents a taxonomic name."""

    value: str
    aliases: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:  # object.__setattr__ as dataclass is frozen
        object.__setattr__(self, "value", self.value.strip())
        object.__setattr__(self, "aliases", frozenset(s for a in self.aliases if (s := a.strip())))

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Represents outcome of a name check against an authority."""

    original_name: TaxonName
    status: NomenStatus
    reason: str
    replacement: TaxonName | None = None
    suggestions: tuple[FuzzyMatch, ...] = field(default_factory=tuple)

    @classmethod
    def valid(cls, name: TaxonName, reason: str) -> Self:
        """Factory method for ValidationResult with NomenStatus.VALID"""
        return cls(name, NomenStatus.VALID, reason, None)

    @classmethod
    def invalid(cls, name: TaxonName, reason: str, replacement: TaxonName | None = None) -> Self:
        """Factory method for ValidationResult with NomenStatus.INVALID (with or without replacement)"""
        status = NomenStatus.INVALID_NO_REPLACEMENT if replacement is None else NomenStatus.INVALID_WITH_REPLACEMENT
        return cls(name, status, reason, replacement)

    @classmethod
    def unmatched(cls, name: TaxonName, reason: str = "Not found in authority") -> Self:
        """Factory method for ValidationResult with NomenStatus.UNMATCHED"""
        return cls(name, NomenStatus.UNMATCHED, reason, None)

    @property
    def is_adopted(self) -> bool:
        return self.status == NomenStatus.VALID
