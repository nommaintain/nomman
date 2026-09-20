# nomman/authorities/assert_list.py
"""Basic data structures for a nomenclature authority, including an user-defined assert list."""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import override

from nomman.exceptions import AuthorityFormatError
from nomman.taxon_interface import NomenMatcher
from nomman.utils import text_open
from nomman.values import TaxonName, ValidationResult


class MedStatus(StrEnum):
    NIL = "nil"
    MED_R = "recommended for medical use"
    MED_NR = "not recommended for medical use"
    INF_A = "inferred as appropriate"
    INF_I = "inferred as inappropriate"
    NO_ALT = "no alternatives"
    NO_ALT_M = "no alternatives & medically important"
    HOMONYM = "same name applied to different taxa"
    DB_CONFLICT = "conflicts within database"


@dataclass(frozen=True, slots=True)
class AssertDb:
    accept: frozenset[str]
    reject: dict[str, str]

    @classmethod
    def load(cls, path: str) -> "AssertDb":
        with text_open(path) as f:
            return _parse_assert_db(f)

    def summarize(self) -> str:
        return f"**accept**: {len(self.accept)}; \n**reject**: {len(self.reject)}"


class AssertMatcher(NomenMatcher):
    """
    Matcher implementation for user-defined assert lists.
    Translates whitelist/blacklist entries into ValidationResults.
    """

    def __init__(self, db: AssertDb) -> None:
        self._db = db

    @override
    def match(self, name: TaxonName) -> ValidationResult:
        if (val := name.value) in self._db.accept:
            return ValidationResult.valid(name, "Assert list: Accept")
        if val in self._db.reject:
            replacement = TaxonName(replace) if (replace := self._db.reject[val]) else None
            return ValidationResult.invalid(name, "Assert list: Reject", replacement)
        return ValidationResult.unmatched(name)

    @override
    def get_all_names(self) -> frozenset[str]:
        return self._db.accept | self._db.reject.keys()


def _parse_reject_entry(line: str) -> tuple[str, str]:
    if not (parts := [p.strip() for p in line.split("||")])[0]:
        raise AuthorityFormatError(f"Assert file: empty origin name in [reject] entry: {line}")
    return parts[0], parts[1] if len(parts) > 1 else ""


def _parse_assert_db(lines: Iterable[str]) -> AssertDb:
    accept: set[str] = set()
    reject: dict[str, str] = {}
    section: str | None = None
    for line in (stripped for raw in lines if (stripped := raw.strip()) and not stripped.startswith("#")):
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section == "accept":
            accept.add(line)
        elif section == "reject":
            origin, replacement = _parse_reject_entry(line)
            reject[origin] = replacement
    if accept.intersection(reject):
        raise AuthorityFormatError("Overlap found between accept and reject lists in assert file.")
    return AssertDb(accept=frozenset(accept), reject=reject)
