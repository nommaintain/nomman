# nomman/gateways/mycb_gateway.py
from collections import defaultdict
from enum import Enum, auto
from typing import override

from nomman.authorities.mycobank import MycobankDb, MycobankRank
from nomman.exceptions import TaxonRankError
from nomman.services.taxon_resolver import HIGHER_TAXON_MARKER
from nomman.taxon_interface import TaxonGateway

GENERA_LIMIT = 500


class _Sentinel(Enum):
    """Sentinel marker to identify taxa which exceeded the member limit."""

    TOO_BROAD = auto()


class MycobankGateway(TaxonGateway):
    def __init__(self, offline_db: MycobankDb, source_file: str):
        self._db = offline_db
        self._source_file = source_file
        self._lineage_index: dict[str, list[str] | _Sentinel] | None = None

    @property
    @override
    def name(self) -> str:
        return "Mycobank Gateway"

    @override
    def exists(self, name: str) -> bool:
        return name in self._db.scorecard

    @override
    def get_genus_members(self, genus: str) -> list[str]:
        return [name for name in self._db.scorecard if name.startswith(f"{genus} ")]

    @override
    def taxon_query(self, name: str) -> tuple[str, list[str]] | None:
        if self._lineage_index is None:
            self._build_lineage_index()
        assert self._lineage_index is not None, "Mycobank gateway lineage index construction failed"
        if (result := self._lineage_index.get(name)) is None:
            return None
        if result is _Sentinel.TOO_BROAD:
            raise TaxonRankError(f"Taxon '{name}' is too broad (>{GENERA_LIMIT} genera); cannot enumerate.")
        # Determine rank of query. Try scorecard first, then fallback to generic identifier.
        rank = t.rank.to_taxonrank.value if (t := self._db.scorecard.get(name)) and t.rank else HIGHER_TAXON_MARKER
        return (rank, result)

    def _build_lineage_index(self) -> None:
        """
        Builds reverse index mapping higher-order taxa to descendant genera.
        Processes all entries with Genus rank, regardless of validity.
        """
        tmp_index: defaultdict[str, list[str]] = defaultdict(list)
        for entry in self._db.entries.values():
            if entry.rank != MycobankRank.GENUS:
                continue
            lineage = [t for token in entry.classification.split(",") if (t := token.strip())]
            for ancestor in lineage:
                tmp_index[ancestor].append(entry.taxon)
        self._lineage_index = {t: _Sentinel.TOO_BROAD if len(g) > GENERA_LIMIT else g for t, g in tmp_index.items()}
