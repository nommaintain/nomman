# nomman/authorities/lpsn.py
"""Operational data structures for LPSN nomenclature authority."""

import csv
import logging
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple, TextIO, override

from nomman.exceptions import AuthorityFormatError
from nomman.taxon_interface import NomenMatcher
from nomman.utils import flex_opener
from nomman.values import TaxonName, ValidationResult

from .base import MedStatus

logger = logging.getLogger(__name__)


LPSN_HEADERS = [
    "genus_name",
    "sp_epithet",
    "subsp_epithet",
    "reference",
    "status",
    "authors",
    "address",
    "risk_grp",
    "nomenclatural_type",
    "record_no",
    "record_lnk",
]


class LornCategory(StrEnum):
    R = "recommended for medical use"
    NR = "not recommended for medical use"
    ND = "no designation"


MEDSTATUS_MAP = {
    "lorn": {LornCategory.R: MedStatus.MED_R, LornCategory.NR: MedStatus.MED_NR},
    "opinion": {
        MedStatus.INF_A: {"correct name", "orphaned species"},
        MedStatus.INF_I: {
            "misspelling",
            "not in use",
            "orphaned subspecies",
            "synonym",
            "synonym of its species",
            "in need of a replacement",
        },
    },
}


class LpsnEntry(NamedTuple):
    taxon: str
    record_no: int
    record_lnk: int
    substatus_lorn: LornCategory
    substatus_opinion: str


@dataclass(frozen=True, slots=True)
class LpsnTaxon:
    name: str
    name_status: MedStatus
    correct_name: str
    c_name_record: int
    entries: tuple[LpsnEntry, ...]

    @staticmethod
    def _calc_status(lorn: LornCategory, opinion: str) -> MedStatus:
        if status := MEDSTATUS_MAP["lorn"].get(lorn):
            return status
        for status, status_set in MEDSTATUS_MAP["opinion"].items():
            if opinion in status_set:
                return status
        return MedStatus.NIL

    @classmethod
    def from_entries(cls, name: str, entries: list[LpsnEntry], e_dict: dict[int, LpsnEntry]) -> "LpsnTaxon":
        """Factory that resolves status and nomenclature before instantiation."""
        n_statuses = [cls._calc_status(e.substatus_lorn, e.substatus_opinion) for e in entries]
        res = cls._resolve_priority(name, entries, n_statuses) or cls._resolve_fallback(entries, n_statuses, e_dict)
        status, c_name, c_record = res
        return cls(name=name, name_status=status, correct_name=c_name, c_name_record=c_record, entries=tuple(entries))

    @staticmethod
    def _follow_link(r_no: int, e_dict: dict[int, LpsnEntry]) -> tuple[str, int]:
        visited: set[int] = set()
        current_id = r_no
        while current_id != 0 and (entry := e_dict.get(current_id)):  # existence check
            if current_id in visited:  # circularity check
                logger.warning(f"Circular reference detected in LPSN DB at record {current_id}")
                return "", 0
            visited.add(current_id)
            if not entry.record_lnk:  # terminal record reached; check if 'good' nomenclature match
                if LpsnTaxon._calc_status(entry.substatus_lorn, entry.substatus_opinion) in (
                    MedStatus.MED_R,
                    MedStatus.INF_A,
                ):
                    return entry.taxon, current_id
                break
            current_id = entry.record_lnk
        return "", 0

    @staticmethod
    def _resolve_priority(
        name: str, entries: list[LpsnEntry], n_statuses: list[MedStatus]
    ) -> tuple[MedStatus, str, int] | None:
        """Finds the best valid entry based on priority order (MED_R > INF_A)."""
        for target in (MedStatus.MED_R, MedStatus.INF_A):
            if target in n_statuses:
                if n_statuses.count(MedStatus.MED_R) + n_statuses.count(MedStatus.INF_A) > 1:
                    logger.warning(f"Multiple good entries for: {name}")
                entry = entries[n_statuses.index(target)]
                return target, entry.taxon, entry.record_no
        return None

    @staticmethod
    def _resolve_fallback(
        entries: list[LpsnEntry], n_statuses: list[MedStatus], entries_dict: dict[int, LpsnEntry]
    ) -> tuple[MedStatus, str, int]:
        """Handles cases where no primary 'good' entry is found by analyzing links and base status."""
        base_status = MedStatus.MED_NR if MedStatus.MED_NR in n_statuses else MedStatus.INF_I
        match len({e.record_lnk for e in entries if e.record_lnk}):
            case n if n > 1:
                return MedStatus.HOMONYM, "", 0
            case 1:
                start_node = next(e for e in entries if e.record_lnk)
                if (result := LpsnTaxon._follow_link(start_node.record_no, entries_dict))[0]:
                    return base_status, result[0], result[1]
        fail_status_map = {MedStatus.MED_NR: MedStatus.NO_ALT_M, MedStatus.INF_I: MedStatus.NO_ALT}
        return fail_status_map[base_status], "", 0


@dataclass(frozen=True, slots=True)
class LpsnDb:
    scorecard: dict[str, LpsnTaxon]
    entries: dict[int, LpsnEntry]

    @classmethod
    def load(cls, filepath: str) -> "LpsnDb":
        """Loads LPSN database from CSV file (raw or compressed)."""
        try:
            with flex_opener(Path(filepath)) as f:
                entries_dict, taxa_entries_map = _process_raw_lpsn(_parse_lpsn_csv(f))
        except OSError as e:
            raise AuthorityFormatError(f"Unable to read LPSN DB file at {filepath}") from e
        scorecard = _handle_lpsn_orphaned_subsp(
            {name: LpsnTaxon.from_entries(name, entries, entries_dict) for name, entries in taxa_entries_map.items()}
        )
        return LpsnDb(scorecard=scorecard, entries=entries_dict)

    def summarize(self) -> str:
        counts = Counter(t.name_status for t in self.scorecard.values()).most_common()
        return "\n".join([f"**{k}**: {v}; " for k, v in counts])


class LpsnMatcher(NomenMatcher):
    """
    Matcher implementation for the LPSN Offline Database.
    Translates internal MedStatus and Correct Name logic into ValidationResults.
    """

    def __init__(self, db: LpsnDb) -> None:
        self._db = db

    @override
    def match(self, name: TaxonName) -> ValidationResult:
        if not (taxon := self._db.scorecard.get(name.value)):
            return ValidationResult.unmatched(name)
        match taxon.name_status:  # Mapping Internal MedStatus -> ValidationResult
            case MedStatus.MED_R:
                return ValidationResult.valid(name, "LPSN: recommended for medical use")
            case MedStatus.INF_A:
                return ValidationResult.valid(name, "LPSN: inferred to be appropriate")
            case MedStatus.MED_NR:
                return self._invalid_with_replacement(name, taxon, "LPSN: not recommended for medical use")
            case MedStatus.INF_I:
                return self._invalid_with_replacement(name, taxon, "LPSN: inferred to be inappropriate")
            case MedStatus.NO_ALT:
                return ValidationResult.invalid(name, "LPSN: no alternatives")
            case MedStatus.NO_ALT_M:
                return ValidationResult.invalid(name, "LPSN: medically important; no alternatives")
            case MedStatus.HOMONYM:
                return ValidationResult.invalid(name, "LPSN: homonym conflict")
            case MedStatus.NIL:
                return ValidationResult.unmatched(name, "LPSN: status not determined")
            case _:
                return ValidationResult.unmatched(name, "LPSN: unknown status")

    @override
    def get_all_names(self) -> frozenset[str]:
        return frozenset(self._db.scorecard.keys())

    @staticmethod
    def _invalid_with_replacement(name: TaxonName, taxon: LpsnTaxon, reason: str) -> ValidationResult:
        replacement = TaxonName(taxon.correct_name) if taxon.correct_name else None
        return ValidationResult.invalid(name, reason, replacement)


def _parse_lpsn_csv(stream: TextIO) -> Iterator[dict[str, str]]:
    if (reader := csv.DictReader(stream)).fieldnames != LPSN_HEADERS:
        raise AuthorityFormatError(f"LPSN DB header mismatch. Expected: {LPSN_HEADERS}")
    yield from reader


def _process_raw_lpsn(rows: Iterator[dict[str, str]]) -> tuple[dict[int, LpsnEntry], dict[str, list[LpsnEntry]]]:
    """Transforms raw LPSN CSV rows into Python structures."""
    entries_dict: dict[int, LpsnEntry] = {}
    taxa_entries: dict[str, list[LpsnEntry]] = {}  # Store lists of entries per name
    for row in rows:
        try:
            entry = _create_lpsn_entry(row)
            entries_dict[entry.record_no] = entry
            taxa_entries.setdefault(entry.taxon, []).append(entry)
        except (ValueError, IndexError) as e:
            logger.warning(f"Skipping malformed LPSN row: {e}")
            continue
    return entries_dict, taxa_entries


def _create_lpsn_entry(row: dict[str, str]) -> LpsnEntry:
    """Parses a single CSV row into an LpsnEntry value object."""
    lorn, opinion = _parse_lorn_status(row["status"])
    return LpsnEntry(
        taxon=_gen_lpsn_taxon_key(row["genus_name"], row["sp_epithet"], row["subsp_epithet"]),
        record_no=int(row["record_no"]),
        record_lnk=int(row["record_lnk"]) if row["record_lnk"] else 0,
        substatus_lorn=lorn,
        substatus_opinion=opinion,
    )


def _gen_lpsn_taxon_key(g: str, sp: str, subsp: str) -> str:
    parts = (g.strip(), sp.strip(), f"subsp. {ss_st}" if (ss_st := subsp.strip()) else "")
    return " ".join(p for p in parts if p)


def _parse_lorn_status(status_str: str) -> tuple[LornCategory, str]:
    """Extracts LORN category and the cleaned status opinion."""
    if len(parts := status_str.split("; ")) != 4:
        raise AuthorityFormatError(f"Incorrect status format: {status_str}")
    status_core = parts[3]
    lorn = LornCategory.ND
    for cat in (LornCategory.NR, LornCategory.R):
        if status_core.endswith(cat):
            lorn = cat
            status_core = status_core.removesuffix(", " + cat)
            break
    return lorn, status_core


def _handle_lpsn_orphaned_subsp(taxa_dict: dict[str, LpsnTaxon]) -> dict[str, LpsnTaxon]:
    """
    Corrects subspecies entries by referencing their basionyms.
    Returns new dictionary with updated LpsnTaxon instances.
    """
    updated_taxa = dict(taxa_dict)
    for t_name, taxon in taxa_dict.items():
        # Check if potential orphaned subspecies: "Genus species subsp. species"
        if (
            taxon.name_status in (MedStatus.NO_ALT, MedStatus.NO_ALT_M)
            and len(words := t_name.split()) == 4
            and words[1] == words[3]
        ):
            basionym_name = f"{words[0]} {words[1]}"
            if basionym := taxa_dict.get(basionym_name):
                # Calculate new status based on first entry of current taxon when resolving 'NO_ALT'
                entry = taxon.entries[0]
                new_status = LpsnTaxon._calc_status(entry.substatus_lorn, entry.substatus_opinion)
                updated_taxa[t_name] = replace(  # NEW frozen instance with updated values
                    taxon,
                    correct_name=basionym.correct_name,
                    c_name_record=basionym.c_name_record,
                    name_status=new_status,
                )
    return updated_taxa
