# nomman/services/fuzzy.py
"""Module for fuzzy match pre-validation logic."""

import logging
import os
import re
import sys
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from typing import Final

import psutil
from rapidfuzz import distance, fuzz, process

from nomman.models import Authority
from nomman.values import FuzzyMatch, TaxonName

logger = logging.getLogger(__name__)

# --- Worker Global State for Fuzzy Matching ---
_AUTH_NAMES: frozenset[str] | None = None


def _init_w(auth_names: frozenset[str], log_level: int) -> None:
    """Initializer for each worker process."""
    global _AUTH_NAMES
    _AUTH_NAMES = auth_names


def _fuzzy_worker(
    name_obj: TaxonName,
    top_hits: int,
    max_suggest: int,
    each_cutoff: float,
    comp_cutoff: float,
) -> tuple[FuzzyMatch, ...]:
    """Standalone worker function to fuzzy match against global reference set."""
    global _AUTH_NAMES
    if _AUTH_NAMES is None:
        return ()
    q = name_obj.value
    _SCORERS: Final = ((fuzz.partial_ratio, 1.0), (fuzz.WRatio, 1.0), (distance.JaroWinkler.similarity, 100.0))
    candidates = {
        match
        for s, mult in _SCORERS
        for match, _, _ in process.extract(q, _AUTH_NAMES, scorer=s, limit=top_hits, score_cutoff=each_cutoff / mult)
    }
    if not candidates:
        return ()
    suggestions = [
        FuzzyMatch(c, comp)
        for c in candidates
        if (comp := sum(s(q, c) * m for s, m in _SCORERS) / len(_SCORERS)) >= comp_cutoff
    ]
    return tuple(sorted(suggestions, key=lambda x: x.score, reverse=True)[:max_suggest])


class FuzzyPrevalidation:
    """Service for identifying/correcting possible typos in lab names by fuzzy match against authorities."""

    _TOP_HITS = 200
    _MAX_SUGGEST = 6
    _EACH_CUTOFF = 80  # normalized from 0-100
    _COMP_CUTOFF = _EACH_CUTOFF * 0.9
    _MIN_CHARS = 4
    _VALID_CHARS_RE = re.compile(r"^[a-zA-Z. \-]+$")
    _MAX_MEM_RATIO = 0.8

    @staticmethod
    def get_auths_names(auths: Iterable[Authority]) -> frozenset[str]:
        """Gets all unique taxon names from loaded authorities."""
        return frozenset().union(*(a.matcher.get_all_names() for a in auths if a.matcher))

    def get_suggestions(
        self, lab_names: frozenset[TaxonName], auth_names: frozenset[str], user_w: int | None = None
    ) -> dict[TaxonName, tuple[FuzzyMatch, ...]]:
        """Returns high-confidence fuzzy matches for names that aren't exact matches."""
        _log_lvl = logging.getLogger().getEffectiveLevel()
        if not (targets := [n for n in lab_names if self._is_target(n.value, auth_names)]):
            return {}
        max_w = self._calc_worker_count(auth_names, user_w)
        logger.info(f"Starting fuzzy match for {len(targets)} targets with {max_w} workers ...")
        worker_func = partial(
            _fuzzy_worker,
            top_hits=self._TOP_HITS,
            max_suggest=self._MAX_SUGGEST,
            each_cutoff=self._EACH_CUTOFF,
            comp_cutoff=self._COMP_CUTOFF,
        )
        with ProcessPoolExecutor(max_workers=max_w, initializer=_init_w, initargs=(auth_names, _log_lvl)) as executor:
            results = executor.map(worker_func, targets)
        return {name: matches for name, matches in zip(targets, results, strict=True) if matches}

    def _calc_worker_count(self, auth_names: frozenset[str], user_w: int | None) -> int:
        """Determines no. of worker processes considering dataset size, system RAM, core count & user preference."""
        cpu_count = max(1, (os.cpu_count() or 1) - 1)
        usable_ram = psutil.virtual_memory().total * self._MAX_MEM_RATIO
        dataset_size = sys.getsizeof(auth_names) + sum(sys.getsizeof(s) for s in auth_names)
        logger.debug(f"Fuzzy match dataset mem usage: {dataset_size / 1048576:.2f} MB")
        logger.debug(f"Presumed a/v mem: {usable_ram / 1048576:.2f}")
        # Constraint: < CPU cores; >= 1
        return max(1, min(int(usable_ram // dataset_size), cpu_count, (user_w or cpu_count)))

    @staticmethod
    def _is_target(
        n: str, namelist: frozenset[str], min_chars: int = _MIN_CHARS, valid_chars_re: re.Pattern[str] = _VALID_CHARS_RE
    ) -> bool:
        return all((n not in namelist, not n.isupper(), len(n) >= min_chars, valid_chars_re.match(n)))
