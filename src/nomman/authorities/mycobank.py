# nomman/authorities/mycobank.py
"""Operational data structures for Mycobank nomenclature authority."""

import csv
import logging
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, replace
from enum import Enum, StrEnum, auto, nonmember
from pathlib import Path
from types import MappingProxyType
from typing import Literal, NamedTuple, override

from python_calamine import CalamineWorkbook

from nomman.exceptions import AuthorityFormatError, ExternalError
from nomman.taxon_interface import NomenMatcher
from nomman.utils import cast_int, flex_opener
from nomman.values import TaxonName, TaxonRank, ValidationResult

from .base import MedStatus

logger = logging.getLogger(__name__)

type MycbNameStatus = Literal[MedStatus.INF_A, MedStatus.INF_I, MedStatus.NO_ALT, MedStatus.DB_CONFLICT]

MYCOBANK_HEADERS = [
    "ID",
    "Taxon name",
    "Authors",
    "Authors (abbreviated)",
    "Rank",
    "Year of effective publication",
    "Name status",
    "MycoBank #",
    "Hyperlink to MB",
    "Classification",
    "Current MycoBank #",
    "Current name",
    "Synonymy",
]

TARGET_COLS = [
    "Taxon name",
    "Rank",
    "Name status",
    "MycoBank #",
    "Classification",
    "Current MycoBank #",
    "Synonymy",
]

assert set(TARGET_COLS).issubset(set(MYCOBANK_HEADERS)), (
    f"Program logic error: missing targets in expected headers: {set(TARGET_COLS) - set(MYCOBANK_HEADERS)}"
)


class MycobankRank(StrEnum):
    GENUS = "gen."
    SPECIES = "sp."
    FAMILY = "fam."
    FORM = "f."
    VARIETY = "var."
    ORDER = "ordo"
    SUBGENUS = "subgen."
    SUBFAMILY = "subfam."
    SECTION = "sect."
    TRIBE = "tr."
    SUBSPECIES = "subsp."
    SUBVARIETY = "subvar."
    SUBSECTION = "subsect."
    SUBCLASS = "subcl."
    SUBDIVISION = "subdiv."
    CLASS = "cl."
    DIVISION = "div."
    SERIES = "ser."
    SUBORDER = "subordo"
    SUBTRIBE = "subtr."
    SUBFORM = "subf."
    KINGDOM = "regn."
    SUBKINGDOM = "subregn."
    SUBSERIES = "subser."
    OTHERS = auto()

    _TAXONRANK_MAP = nonmember(
        MappingProxyType(
            {
                GENUS: TaxonRank.GENUS,
                SPECIES: TaxonRank.SPECIES,
                FAMILY: TaxonRank.FAMILY,
                FORM: TaxonRank.SUBSPECIES,
                VARIETY: TaxonRank.SUBSPECIES,
                ORDER: TaxonRank.ORDER,
                SUBGENUS: TaxonRank.GENUS,
                SUBFAMILY: TaxonRank.FAMILY,
                SECTION: TaxonRank.GENUS,
                TRIBE: TaxonRank.FAMILY,
                SUBSPECIES: TaxonRank.SUBSPECIES,
                SUBVARIETY: TaxonRank.SUBSPECIES,
                SUBSECTION: TaxonRank.GENUS,
                SUBCLASS: TaxonRank.CLASS,
                SUBDIVISION: TaxonRank.PHYLUM,
                CLASS: TaxonRank.CLASS,
                DIVISION: TaxonRank.PHYLUM,
                SERIES: TaxonRank.GENUS,
                SUBORDER: TaxonRank.ORDER,
                SUBTRIBE: TaxonRank.FAMILY,
                SUBFORM: TaxonRank.SUBSPECIES,
                KINGDOM: TaxonRank.KINGDOM,
                SUBKINGDOM: TaxonRank.KINGDOM,
                SUBSERIES: TaxonRank.GENUS,
                OTHERS: TaxonRank.INVALID,
            }
        )
    )

    @property
    def to_taxonrank(self) -> TaxonRank:
        return self._TAXONRANK_MAP.get(self, TaxonRank.INVALID)


class MycbResolution(NamedTuple):
    status: MycbNameStatus
    name: str
    record: int
    rank: MycobankRank | None


class MycobankStatus(StrEnum):
    NIL = auto()
    DELETED = "Deleted"
    ILLEGIT = "Illegitimate"
    INVALID = "Invalid"
    LEGIT = "Legitimate"
    ORTHO_V = "Orthographic variant"
    UNAVAIL = "Unavailable"
    UNCERTAIN = "Uncertain"


class MycobankEntry(NamedTuple):
    taxon: str
    rank: MycobankRank
    record_no: int
    name_status: MycobankStatus
    current_lnk: int
    classification: str


class MycbTaxonConflict(Enum):
    CLEAN = 0
    MINOR = 1
    MAJOR = 2
    CRITICAL = 3


_MB_PATTERN = re.compile(r"\[MB#(.*?)\]")
_RANK_MAP = {r.value: r for r in MycobankRank}
_STATUS_MAP = {s.value: s for s in MycobankStatus}


@dataclass(frozen=True, slots=True)
class MycobankTaxon:
    name: str
    name_status: MycbNameStatus
    conflict_status: MycbTaxonConflict
    correct_name: str
    c_name_record: int
    rank: MycobankRank
    entries: tuple[MycobankEntry, ...]

    @classmethod
    def from_entries(cls, entries: list[MycobankEntry], e_dict: dict[int, MycobankEntry]) -> "MycobankTaxon":
        """Factory resolving overall status and conflict level for a taxon based on associated entries."""
        local_resolutions = [cls._resolve_local(e, e_dict) for e in entries]
        a_res, conflict = cls._resolve_aggregate(local_resolutions)
        assert a_res.rank is not None  # _resolve_aggregate() always resolves rank
        return cls(
            name=entries[0].taxon,
            name_status=a_res.status,
            conflict_status=conflict,
            correct_name=a_res.name,
            c_name_record=a_res.record,
            rank=a_res.rank,
            entries=tuple(entries),
        )

    @classmethod
    def from_entry(cls, entry: MycobankEntry, e_dict: dict[int, MycobankEntry]) -> "MycobankTaxon":
        """Factory for a taxon that has single entry."""
        return (
            cls(
                name=entry.taxon,
                name_status=MedStatus.DB_CONFLICT,
                conflict_status=MycbTaxonConflict.CRITICAL,
                correct_name="",
                c_name_record=0,
                rank=MycobankRank.OTHERS,
                entries=(entry,),
            )
            if (res := cls._resolve_local(entry, e_dict)).status == MedStatus.DB_CONFLICT
            else cls(
                name=entry.taxon,
                name_status=res.status,
                conflict_status=MycbTaxonConflict.CLEAN,
                correct_name=res.name,
                c_name_record=res.record,
                rank=res.rank or MycobankRank.OTHERS,
                entries=(entry,),
            )
        )

    @staticmethod
    def _resolve_local(entry: MycobankEntry, e_dict: dict[int, MycobankEntry]) -> MycbResolution:
        target = e_dict.get(entry.current_lnk)
        if entry.name_status == MycobankStatus.LEGIT:
            # Rules 1 & 2: Legit name, links to self or same-name record
            if entry.current_lnk == entry.record_no or (target and target.taxon == entry.taxon):
                return MycbResolution(MedStatus.INF_A, entry.taxon, entry.record_no, entry.rank)
            # Rule 3a: If linked record is legit and stable (points to itself), accept as replacement
            if target and target.name_status == MycobankStatus.LEGIT and target.current_lnk == target.record_no:
                return MycbResolution(MedStatus.INF_I, target.taxon, target.record_no, target.rank)
            # Rule 3b: Is DB conflict if link is broken or points to an untrustworthy record
            return MycbResolution(MedStatus.DB_CONFLICT, "", 0, None)
        if entry.current_lnk == entry.record_no:  # Rule 6: Not legit, but links to itself
            return MycbResolution(MedStatus.DB_CONFLICT, "", 0, None)
        # Rules 4 & 5: Not legit, check target record
        if target and target.name_status == MycobankStatus.LEGIT and target.current_lnk == target.record_no:
            if target.taxon == entry.taxon:  # Rule 4 Upgrade: target is legit/self-linking and has same name
                return MycbResolution(MedStatus.INF_A, target.taxon, target.record_no, target.rank)
            # legit/self-linking, different name
            return MycbResolution(MedStatus.INF_I, target.taxon, target.record_no, target.rank)
        # Rule 5: Not legit, target unresolvable or not a legit record
        return MycbResolution(MedStatus.NO_ALT, "", 0, None)

    @staticmethod
    def _resolve_aggregate(res: list[MycbResolution]) -> tuple[MycbResolution, MycbTaxonConflict]:
        statuses = [r.status for r in res]
        # Rule 10: Multiple inappropriate status with different correct names
        inf_i_targets = {r.name for r in res if r.status == MedStatus.INF_I and r.name}
        is_downgraded_rule_10 = len(inf_i_targets) > 1
        # Rule 9: Rank conflict check among all appropriate/inappropriate entries
        valid_ranks = {r.rank for r in res if r.status in (MedStatus.INF_A, MedStatus.INF_I) and r.rank}
        has_rank_conflict = len(valid_ranks) > 1
        resolved_rank = MycobankTaxon._extract_rank(res, MycobankRank.OTHERS, has_rank_conflict)
        overall_status = MycobankTaxon._calc_overall_status(statuses, is_downgraded_rule_10)
        c_name, c_record = MycobankTaxon._get_correct(res, overall_status)
        conflict_status = MycobankTaxon._calc_conflict_level(
            statuses, overall_status, has_rank_conflict, is_downgraded_rule_10
        )
        return MycbResolution(overall_status, c_name, c_record, resolved_rank), conflict_status

    @staticmethod
    def _extract_rank(res: list[MycbResolution], fallback: MycobankRank, rank_conflict: bool) -> MycobankRank:
        if rank_conflict:
            return fallback
        TARGET_STATUSES = (MedStatus.INF_A, MedStatus.INF_I)
        return next((r.rank for r in res if r.status in TARGET_STATUSES and r.rank), fallback)

    @staticmethod
    def _calc_overall_status(statuses: list[MedStatus], is_downgraded: bool) -> MycbNameStatus:
        if is_downgraded:  # Rule 10
            return MedStatus.DB_CONFLICT
        # Rule 7: Priority order: Appropriate > Inappropriate > No Alt > DB Conflict
        for priority in (MedStatus.INF_A, MedStatus.INF_I, MedStatus.NO_ALT):
            if priority in statuses:
                return priority
        return MedStatus.DB_CONFLICT

    @staticmethod
    def _get_correct(res: list[MycbResolution], overall_status: MycbNameStatus) -> tuple[str, int]:
        """Resolves correct name and record (from first appropriate/inappropriate entry)."""
        if overall_status == MedStatus.DB_CONFLICT:
            return "", 0
        TARGET_STATUSES = (MedStatus.INF_A, MedStatus.INF_I)
        return next(((r.name, r.record) for r in res if r.status in TARGET_STATUSES), ("", 0))

    @staticmethod
    def _calc_conflict_level(
        statuses: list[MedStatus], overall_status: MycbNameStatus, has_rank_conflict: bool, is_downgraded: bool
    ) -> MycbTaxonConflict:
        if (  # Rules 8 & 9: Critical conflict checks
            has_rank_conflict
            or (MedStatus.INF_A in statuses and MedStatus.INF_I in statuses)
            or (len(set(statuses)) == 1 and overall_status == MedStatus.DB_CONFLICT)
            or is_downgraded
        ):
            return MycbTaxonConflict.CRITICAL
        db_conflict_count = statuses.count(MedStatus.DB_CONFLICT)  # Rule 8: DB Conflict counts for Major/Minor
        if db_conflict_count >= 2:
            return MycbTaxonConflict.MAJOR
        if db_conflict_count == 1:
            return MycbTaxonConflict.MINOR
        return MycbTaxonConflict.CLEAN


@dataclass(frozen=True, slots=True)
class MycobankDb:
    scorecard: dict[str, MycobankTaxon]
    entries: dict[int, MycobankEntry]
    skipped: dict[str, int]

    @classmethod
    def load(cls, filepath: str) -> "MycobankDb":
        """Loads Mycobank database from local file (XLSX/ CSV/ compressed CSV)."""
        path = Path(filepath)
        if (suffix := path.suffix.lower()) not in (".csv", ".gz", ".xz", ".xlsx"):
            raise AuthorityFormatError(f"Unsupported Mycobank file format: {suffix}")
        rows_iter = cls._read_mycb_xlsx(path) if suffix == ".xlsx" else cls._read_mycb_csv(path)
        entries_dict, skipped_counts = _process_mycb_rows(rows_iter, path.name)
        taxa_entries_map = _gen_mycb_entries_map(entries_dict)
        scorecard = {
            name: MycobankTaxon.from_entry(entries[0], entries_dict)
            if len(entries) == 1  # separate method for single entry because speed
            else MycobankTaxon.from_entries(entries, entries_dict)
            for name, entries in taxa_entries_map.items()
        }
        return MycobankDb(scorecard=scorecard, entries=entries_dict, skipped=skipped_counts)

    @staticmethod
    def _read_mycb_csv(path: Path) -> Iterator[list]:
        """Reads Mycobank data from CSV (also supports .gz, .xz)."""
        try:
            with flex_opener(path) as f:
                yield from csv.reader(f)
        except OSError as e:
            raise ExternalError(f"Failed to read Mycobank CSV file at '{path}': {e}") from e

    @staticmethod
    def _read_mycb_xlsx(path: Path) -> Iterator[list]:
        wb = None
        try:
            wb = CalamineWorkbook.from_path(path)
            if not (sheet := wb.get_sheet_by_index(0)):
                raise AuthorityFormatError(f"Mycobank XLSX file '{path}' has no sheets.")
            yield from sheet.to_python()
        except Exception as e:
            if isinstance(e, AuthorityFormatError):
                raise
            raise ExternalError(f"Failed to read Mycobank XLSX file at '{path}': {e}") from e
        finally:
            if wb is not None:
                wb.close()

    def summarize(self) -> str:
        counts = Counter(t.name_status for t in self.scorecard.values()).most_common()
        lines = [f"**{k}**: {v}" for k, v in counts]
        if self.skipped:
            lines.append("    - Skipped entries: ")
            lines.extend(f"**{reason}**: {count}; " for reason, count in self.skipped.items())
        conflict_counts = Counter(t.conflict_status for t in self.scorecard.values()).most_common()
        lines.append("    - Conflicts: ")
        lines.extend(f"**{c.name.lower()}**: {v}; " for c, v in conflict_counts if c != MycbTaxonConflict.CLEAN)
        return "\n".join(lines)


class MycobankMatcher(NomenMatcher):
    def __init__(self, db: MycobankDb) -> None:
        self._db = db

    @override
    def match(self, name: TaxonName) -> ValidationResult:
        if not (taxon := self._db.scorecard.get(name.value)):
            return ValidationResult.unmatched(name)
        if taxon.conflict_status == MycbTaxonConflict.CRITICAL:
            return ValidationResult.invalid(name, "Mycobank: indeterminate due to DB conflicts")
        res = self._get_base_validation(name, taxon)
        if taxon.conflict_status == MycbTaxonConflict.MAJOR:
            return replace(res, reason=f"{res.reason};(warning: major conflict with other entries of same name)")
        return res

    @override
    def get_all_names(self) -> frozenset[str]:
        return frozenset(self._db.scorecard.keys())

    @staticmethod
    def _get_base_validation(name: TaxonName, taxon: MycobankTaxon) -> ValidationResult:
        match taxon.name_status:  # Mapping Internal MedStatus -> ValidationResult
            case MedStatus.INF_A:
                return ValidationResult.valid(name, "Mycobank: inferred to be appropriate")
            case MedStatus.INF_I:
                replacement = TaxonName(taxon.correct_name) if taxon.correct_name else None
                return ValidationResult.invalid(name, "Mycobank: inferred to be inappropriate", replacement)
            case MedStatus.NO_ALT:
                return ValidationResult.invalid(name, "Mycobank: no alternatives")
            case MedStatus.DB_CONFLICT:
                return ValidationResult.invalid(name, "Mycobank: indeterminate due to DB conflicts")
            case _:
                return ValidationResult.unmatched(name, "Mycobank: unknown status")


def _gen_mycb_entries_map(e_dict: dict[int, MycobankEntry]) -> dict[str, list[MycobankEntry]]:
    e_map: dict[str, list[MycobankEntry]] = defaultdict(list)
    for entry in e_dict.values():
        e_map[entry.taxon].append(entry)
    return dict(e_map)


def _get_mycb_header_map(row: list, pathname: str) -> dict[str, int]:
    col_labels = [str(h).strip() if h is not None else "" for h in row]
    if col_labels[: len(MYCOBANK_HEADERS)] != MYCOBANK_HEADERS:
        raise AuthorityFormatError(f"Mycobank file '{pathname}' headers do not match expected schema.")
    logger.debug("Mycobank headers match expected schema.")
    return {name: index for index, name in enumerate(col_labels)}


def _process_mycb_rows(rows_iter: Iterator[list], pathname: str) -> tuple[dict[int, MycobankEntry], dict[str, int]]:
    try:
        first_row = next(it := iter(rows_iter))
    except StopIteration as e:
        raise AuthorityFormatError(f"Mycobank file '{pathname}' is empty.") from e
    headers = _get_mycb_header_map(first_row, pathname)
    db_entries: dict[int, MycobankEntry] = {}
    skipped_counts: dict[str, int] = defaultdict(int)
    mycb_no_idx = headers["MycoBank #"]
    for row_idx, raw_row in enumerate(it, start=2):
        if not any(raw_row):
            continue
        try:
            if (raw_mycb_no := raw_row[mycb_no_idx]) is None:
                skipped_counts["Missing or discrepant info"] += 1
                continue
            if (record_no := cast_int(raw_mycb_no)) in db_entries:
                logger.debug(f"Mycobank row {row_idx}: duplicate Mycobank #{record_no}.")
                skipped_counts["Duplicate Mycobank #"] += 1
                continue
        except (ValueError, IndexError):
            skipped_counts["Missing or discrepant info"] += 1
            continue
        entry, reason = _parse_single_mycb_row(raw_row, headers, row_idx, record_no)
        if reason is not None:
            skipped_counts[reason] += 1
            continue
        db_entries[record_no] = entry  # type: ignore
    return db_entries, dict(skipped_counts)


def _parse_single_mycb_row(
    raw_row: list, headers: dict[str, int], row_idx: int, record_no: int
) -> tuple[MycobankEntry, None] | tuple[None, str]:
    if not (lineage := str(raw_row[headers["Classification"]])) or not lineage.startswith("Fungi"):
        logger.debug(f"Mycobank row {row_idx}: entry is non-fungal taxon.")
        return None, "Non-fungal taxon"
    if "Fossil " in lineage:
        logger.debug(f"Mycobank row {row_idx}: entry is non-contemporary taxon.")
        return None, "Non-contemporary taxon"
    synonymy = str(raw_row[headers["Synonymy"]] or "")
    extracted_cmb = match.group(1) if (match := _MB_PATTERN.search(synonymy)) else ""
    cmb_no = str(raw_row[headers["Current MycoBank #"]] or "").strip()
    taxon_name = str(raw_row[headers["Taxon name"]] or "").strip()
    if not all((taxon_name, cmb_no, extracted_cmb)) or cmb_no != extracted_cmb:
        logger.debug(f"Mycobank row {row_idx}: entry has missing or discrepant info.")
        return None, "Missing or discrepant info"
    return MycobankEntry(
        taxon=taxon_name,
        rank=_match_mycbrank(str(raw_row[headers["Rank"]] or ""), row_idx),
        record_no=record_no,
        name_status=_match_mycbstatus(str(raw_row[headers["Name status"]] or ""), row_idx),
        current_lnk=cast_int(cmb_no),
        classification=str(lineage).strip(),
    ), None


def _match_mycbstatus(s: str, row_idx: int) -> MycobankStatus:
    if (status := _STATUS_MAP.get(s)) is not None:
        return status
    logger.debug(f"Mycobank file row {row_idx} has invalid name status: {s}; converted to 'nil'")
    return MycobankStatus.NIL


def _match_mycbrank(r: str, row_idx: int) -> MycobankRank:
    if (rank := _RANK_MAP.get(r)) is not None:
        return rank
    logger.debug(f"Mycobank file row {row_idx} has invalid rank: {r}; converted to 'others'")
    return MycobankRank.OTHERS
