# nomman/services/normalize.py
"""
Service for normalizing taxonomic names with common formatting deviations.
Also supports taxon splitting (e.g., 'Proteus mirabilis/vulgaris').
"""

import logging
import re
from dataclasses import dataclass
from enum import StrEnum, auto
from functools import lru_cache
from typing import Final, NamedTuple

from nomman.values import TaxonName

logger = logging.getLogger(__name__)
_MAX_CACHE: Final = 4096


class NormCategory(StrEnum):
    """Categories of normalization applied to a taxon name."""

    BRACKETS = auto()
    WHITESPACE = auto()
    ABBREV = auto()
    INFORMAL = auto()
    MULTIPLE = auto()


class NormRule(NamedTuple):
    """Defines a single normalization transformation."""

    pattern: re.Pattern
    replace: str
    category: NormCategory


@dataclass(frozen=True, slots=True)
class NormResult:
    """Holds result of taxon normalization."""

    name: TaxonName
    is_modified: bool
    category: NormCategory | None = None


class TaxonNormalizer:
    """Handles common nomenclature deviations by normalizing to standard format"""

    # Matches 'subsp', 'ssp', 'subsp.' (case insensitive) followed by a space or end of string
    _SUB_SPEC_PATTERN: Final = re.compile(r"(?<=\S\s)((ssp\.?)|(subsp(?!(\.))))(?=\s\S)", re.IGNORECASE)
    _RULES: Final[tuple[NormRule, ...]] = (
        NormRule(re.compile(r"\(.*?\)", re.IGNORECASE), "", NormCategory.BRACKETS),
        NormRule(re.compile(r"\s{2,}"), " ", NormCategory.WHITESPACE),  # multipe whitespaces
        NormRule(_SUB_SPEC_PATTERN, "subsp.", NormCategory.ABBREV),  # correct variants of 'subsp.'
        NormRule(re.compile(r"\s+spp?\.?$", re.IGNORECASE), "", NormCategory.ABBREV),  # remove 'spp.'/'sp.' suffixes
        NormRule(re.compile(r"\s+(complex|group)$", re.IGNORECASE), "", NormCategory.INFORMAL),  # remove informal grps
    )

    @staticmethod
    @lru_cache(maxsize=_MAX_CACHE)
    def normalize(taxon: TaxonName) -> NormResult:
        """Sequentially applies normalization rules to a taxon name."""
        current_val = taxon.value
        applied_cats: set[NormCategory] = set()
        for rule in TaxonNormalizer._RULES:
            if rule.pattern.search(current_val):
                current_val = rule.pattern.sub(rule.replace, current_val)
                applied_cats.add(rule.category)
        if not applied_cats:
            return NormResult(name=taxon, is_modified=False)
        current_val = current_val.strip()
        if not any(c.isalnum() for c in current_val):
            logger.debug(f"Normalization resulted in empty/invalid name for '{taxon.value}'; reverting.")
            return NormResult(name=taxon, is_modified=False)
        final_cat = NormCategory.MULTIPLE if len(applied_cats) > 1 else next(iter(applied_cats))
        logger.debug(f"Normalize: '{taxon.value}' -> '{current_val}'")
        return NormResult(
            name=TaxonName(value=current_val, aliases=taxon.aliases | {f"{taxon.value} [{final_cat}]"}),
            is_modified=True,
            category=final_cat,
        )


class TaxonSplitter:
    """Expands combined taxon entries into individual TaxonName objects."""

    _DELIMITERS: Final = "/\\"
    _SPLIT_RE: Final = re.compile(rf"\s*(?:[{re.escape(_DELIMITERS)}])\s*")
    _MAX_DERIVES = 4

    @staticmethod
    @lru_cache(maxsize=_MAX_CACHE)
    def split(taxon: TaxonName) -> tuple[TaxonName, ...]:
        """Splits taxon name with combined epithets (e.g. X/Y/Z)."""
        val = taxon.value
        if len({c for c in val if c in TaxonSplitter._DELIMITERS}) != 1:  # only 1 type of delimiter may be used
            return (taxon,)
        if not (2 <= len(segments := TaxonSplitter._SPLIT_RE.split(val)) <= TaxonSplitter._MAX_DERIVES):
            if len(segments) > TaxonSplitter._MAX_DERIVES:
                logger.warning(f"Taxon split: too many derivatives in '{val}' (max {TaxonSplitter._MAX_DERIVES}).")
            return (taxon,)
        if any(not any(c.isalnum() for c in s) for s in segments):
            logger.debug(f"Split skipped for '{val}': empty or non-alphanumeric segments.")
            return (taxon,)
        base, suffixes = segments[0], segments[1:]
        if any(" " in s for s in suffixes):  # split only for the last epithet, & suffix must be single word.
            logger.debug(f"Split rejected for '{val}': Suffixes must be single epithets.")
            return (taxon,)
        # Identify prefix (everything in base except the last word)
        prefix = " ".join(b_parts[:-1]) if len(b_parts := base.split()) > 1 else ""
        # Construct full names: [Base, Prefix + Suffix1, Prefix + Suffix2...]
        result_vals = [base, *(f"{prefix} {s}".strip() for s in suffixes)]
        total = len(result_vals)
        final_taxa = tuple(  # unique alias suffixes for split results: "Original alias (1/3)"
            TaxonName(value=v, aliases=frozenset(f"{a} ({i}/{total})" for a in taxon.aliases))
            for i, v in enumerate(result_vals, start=1)
        )
        logger.debug(f"Split taxon: '{val}' -> {[r.value for r in final_taxa]}")
        return final_taxa
