# nomman/gateways/lpsn_gateway.py
from collections import defaultdict
from collections.abc import Iterable
from functools import cached_property
from typing import override

from nomman.authorities.lpsn import LpsnDb
from nomman.credentials.providers import PwdProvider
from nomman.taxon_interface import TaxonGateway

from . import lpsn_api


class HybridLpsnGateway(TaxonGateway):
    def __init__(self, offline_db: LpsnDb, api_user: str, pwd_provider: PwdProvider):
        self._offline_db = offline_db
        self._api_user = api_user
        lpsn_api.set_password_provider(pwd_provider)

    @cached_property
    def _genus_index(self) -> dict[str, list[str]]:
        """Lazily builds and caches the genus lookup."""
        return self._build_genus_index(self._offline_db.scorecard.keys())

    @staticmethod
    def _build_genus_index(names: Iterable[str]) -> dict[str, list[str]]:
        index = defaultdict(list)
        for name in names:
            index[name.split(" ", 1)[0]].append(name)  # First word is the genus
        return dict(index)

    @property
    @override
    def name(self) -> str:
        return f"LPSN Gateway ({self._api_user})"

    def __str__(self) -> str:
        return self.name

    @override
    def exists(self, name: str) -> bool:
        return name in self._offline_db.scorecard  # Check offline DB first for genus/species

    @override
    def get_genus_members(self, genus: str) -> list[str]:
        return self._genus_index.get(genus, [])  # Find all names in scorecard that start with "Genus"

    @override
    def taxon_query(self, name: str) -> tuple[str, list[str]] | None:
        return lpsn_api.taxon_query(name, self._api_user)
