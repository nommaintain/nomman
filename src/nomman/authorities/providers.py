# nomman/authorities/providers.py
"""Module holding AuthSpecs of nomenclature authorities."""

from typing import override

from nomman.config import ProgConfig
from nomman.credentials.providers import PwdProvider
from nomman.gateways.lpsn_gateway import HybridLpsnGateway
from nomman.gateways.mycb_gateway import MycobankGateway
from nomman.models import Authority
from nomman.repositories.auth_repo import AuthorityProvider
from nomman.utils import calc_checksum

from .base import AssertDb, AssertMatcher
from .lpsn import LpsnDb, LpsnMatcher
from .mycobank import MycobankDb, MycobankMatcher


class AssertListProvider(AuthorityProvider):
    @property
    @override
    def name(self) -> str:
        return "Assert list"

    @override
    def create_authority(self, config: ProgConfig) -> Authority | None:
        if not config.assert_file or config.assert_rank < 0:
            return None
        db = AssertDb.load(config.assert_file)
        return Authority(
            name=self.name,
            rank=config.assert_rank,
            source_file=config.assert_file,
            checksum=calc_checksum(config.assert_file),
            summary=db.summarize(),
            matcher=AssertMatcher(db),
            gateway=None,  # Assert lists don't support expansion
        )


class LpsnDbProvider(AuthorityProvider):
    def __init__(self, pwd_provider: PwdProvider) -> None:
        self._pwd_provider = pwd_provider

    @property
    @override
    def name(self) -> str:
        return "LPSN DB"

    @override
    def create_authority(self, config: ProgConfig) -> Authority | None:
        if not config.lpsn_file or config.lpsn_rank < 0:
            return None
        db = LpsnDb.load(config.lpsn_file)
        return Authority(
            name=self.name,
            rank=config.lpsn_rank,
            source_file=config.lpsn_file,
            checksum=calc_checksum(config.lpsn_file),
            summary=db.summarize(),
            matcher=LpsnMatcher(db),
            gateway=HybridLpsnGateway(offline_db=db, api_user=config.lpsn_user, pwd_provider=self._pwd_provider),
        )


class MycobankDbProvider(AuthorityProvider):
    @property
    @override
    def name(self) -> str:
        return "Mycobank DB"

    @override
    def create_authority(self, config: ProgConfig) -> Authority | None:
        if not config.mycb_file or config.mycb_rank < 0:
            return None
        db = MycobankDb.load(config.mycb_file)
        return Authority(
            name=self.name,
            rank=config.mycb_rank,
            source_file=config.mycb_file,
            checksum=calc_checksum(config.mycb_file),
            summary=db.summarize(),
            matcher=MycobankMatcher(db),
            gateway=MycobankGateway(offline_db=db, source_file=config.mycb_file),
        )


def get_nomen_providers(pwd_provider: PwdProvider) -> list[AuthorityProvider]:
    """Factory that returns the standard list of nomenclature providers."""
    return [AssertListProvider(), LpsnDbProvider(pwd_provider=pwd_provider), MycobankDbProvider()]
