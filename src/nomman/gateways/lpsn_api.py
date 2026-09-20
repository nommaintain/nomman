# nomman/gateways/lpsn_api.py
"""Module holding functions related to accessing LPSN API"""

import logging
from enum import StrEnum, auto
from functools import total_ordering
from itertools import chain
from typing import ReadOnly, TypedDict, cast

import lpsn

import nomman.utils as utils
from nomman.credentials.providers import PwdProvider
from nomman.exceptions import ConfigError, ExternalApiError, TaxonRankError, TaxonResolutionError

type TaxonQueryResult = tuple[LpsnRank, list[str]]

logger = logging.getLogger(__name__)
_pwd_provider: PwdProvider | None = None
session_pw: str = ""


class LpsnAuthenticationError(ExternalApiError):
    """Exception raised for errors during LPSN API authentication."""

    def __init__(self, message: str = "Authentication failed or was cancelled by user.") -> None:
        self.message = message
        super().__init__(self.message)


@total_ordering
class OrderedStrEnum(StrEnum):
    def __lt__(self, other) -> bool:  # noqa: ANN001
        return (
            (members := list(self.__class__)).index(self) < members.index(other)
            if isinstance(other, OrderedStrEnum)
            else NotImplemented
        )


class LpsnRank(OrderedStrEnum):
    DOMAIN = auto()
    KINGDOM = auto()
    PHYLUM = auto()
    CLASS = auto()
    SUBCLASS = auto()
    ORDER = auto()
    SUBORDER = auto()
    FAMILY = auto()
    TRIBE = auto()
    GENUS = auto()
    SUBGENUS = auto()
    SPECIES = auto()
    SUBSPECIES = auto()
    INVALID_TAXON = auto()


class LpsnApiResponse(TypedDict):
    # Use ReadOnly to avoid accidental modification of API data
    id: ReadOnly[int]
    full_name: ReadOnly[str]
    category: ReadOnly[str]
    lpsn_correct_name_id: ReadOnly[int]
    monomial: ReadOnly[str]


def set_password_provider(provider: PwdProvider) -> None:
    """Configures the provider used for LPSN authentication."""
    global _pwd_provider
    _pwd_provider = provider


def taxon_query(
    name: str, api_user: str, id: int = -1, standalone: bool = False, visited: set[int] | None = None
) -> TaxonQueryResult | None:
    visited = visited or set()
    if id != -1:
        if id in visited:
            raise ExternalApiError(f"Circular reference detected in LPSN API at taxon ID {id}")
        visited.add(id)
    api_pw = _lpsn_query_setup(name, api_user, id, standalone, taxon_query.__qualname__)
    client = lpsn.LpsnClient(user=api_user, password=api_pw, max_retries=3, retry_delay=10, request_timeout=30)
    results_count = client.flex_search(search={"full_name": name}) if name else client.search(id=id)
    if standalone:
        _standalone_report(results_count, list(client.retrieve()))
        return None
    match results_count:
        case 0:  # not valid taxon
            return LpsnRank.INVALID_TAXON, []
        case 1:  # check if correct (& get members)
            result = cast(LpsnApiResponse, next(client.retrieve()))
            return _parse_single_result(result, taxon_query.__qualname__, api_user, visited)
        case _:  # >1 entries with same name; see if at least one will lead to correct name
            results = list(client.retrieve())
            if result := _select_best_result(results):
                return _parse_single_result(result, taxon_query.__qualname__, api_user, visited)
            return LpsnRank.INVALID_TAXON, []


def _select_best_result(results: list[dict]) -> LpsnApiResponse | None:
    correct_results = [
        r
        for r in results
        if r["lpsn_correct_name_id"] == r["id"] and not (_match_rank(utils.cast_str(r["category"]))).startswith("sub")
    ]
    match len(correct_results):
        case 0:  # no 'proper' valid taxa
            return None
        case 1:
            return cast(LpsnApiResponse, correct_results[0])
    name = utils.cast_str(correct_results[0]["full_name"])
    raise ValueError(f"Multiple valid & correct taxa entries: {name}")


def _parse_single_result(result: LpsnApiResponse, funcname: str, api_user: str, visited: set[int]) -> TaxonQueryResult:
    if result["lpsn_correct_name_id"] == result["id"]:  # correct taxon
        return _treewalk_single_taxon(result, funcname, api_user)
    else:  # retrieve correct taxon with new id
        q_result = taxon_query("", api_user, utils.cast_int(result["lpsn_correct_name_id"]), visited=visited)
        assert q_result is not None
        return q_result


def _treewalk_validate_rank(name: str, rank: LpsnRank, funcname: str) -> None:
    if rank in (LpsnRank.SPECIES, LpsnRank.SUBSPECIES):
        raise TaxonRankError(f"Monomial name input to {funcname} should not be below genus.")
    if rank < LpsnRank.ORDER:
        raise TaxonRankError(f"Taxon {name} ({rank}) is above Order; cannot enumerate.")
    if rank.startswith("sub") or rank == LpsnRank.TRIBE:
        raise TaxonRankError(f"Rank {rank} (for {name}) is not supported.")


def _treewalk_single_taxon(result: LpsnApiResponse, funcname: str, api_user: str) -> TaxonQueryResult:
    id = utils.cast_int(result["id"])
    name = utils.cast_str(result["full_name"])
    rank = _match_rank(utils.cast_str(result["category"]))
    _treewalk_validate_rank(name, rank, funcname)
    match rank:
        case LpsnRank.GENUS:
            return rank, [name]
        case LpsnRank.ORDER:
            return rank, _get_genera_from_order(id, api_user)
        case LpsnRank.FAMILY:
            return rank, _get_genera_from_family(id, api_user)
        case _:
            raise RuntimeError(f"Unexpected rank logic failure for: {name} >> {rank}")


def _lpsn_query_get_children(id: int, api_user: str, filter: list[str]) -> list[dict]:
    client = lpsn.LpsnClient(user=api_user, password=session_pw, max_retries=3, retry_delay=10, request_timeout=30)
    return list(client.retrieve(filter=filter)) if client.flex_search(search={"lpsn_parent_id": id}) else []


def _get_genera_from_order(id: int, api_user: str) -> list[str]:
    if not (children := _lpsn_query_get_children(id, api_user, ["monomial", "id", "lpsn_correct_name_id", "category"])):
        return []
    assert children[0].get("category") == LpsnRank.FAMILY
    g_list = [
        _get_genera_from_family(utils.cast_int(s.get("id")), api_user)
        for s in children
        if s.get("id") == s.get("lpsn_correct_name_id")
    ]
    return list(chain.from_iterable(g_list))


def _get_genera_from_family(id: int, api_user: str) -> list[str]:
    if not (children := _lpsn_query_get_children(id, api_user, ["monomial", "category"])):
        return []
    assert children[0].get("category") == LpsnRank.GENUS
    return [utils.cast_str(s.get("monomial")) for s in children]


def _lpsn_query_setup(name: str, api_user: str, id: int, standalone: bool, funcname: str) -> str:
    if not name and id < 0:
        raise TaxonResolutionError(f"No organism identifiers (name or id) provided to {funcname}")
    if name and not standalone:
        assert len(name.split()) == 1, f"Name input to {funcname} function call should be monomial"
    if not api_user:
        raise ConfigError("LPSN API query attempted without username specified in config ")
    global session_pw
    if not session_pw:
        if _pwd_provider is None:
            raise RuntimeError("LPSN API password provider not configured.")
        if (pwd_candidate := _pwd_provider.get_pwd(api_user)) is None:
            raise LpsnAuthenticationError(f"User declined to provide password for {api_user}.")
        session_pw = pwd_candidate
    return session_pw


def _standalone_report(count: int, results: list) -> None:
    print(f"Count is {count}")
    if count:
        for i, n in enumerate(results):
            print(f"{i}: {n}")
    return


def _match_rank(c: str) -> LpsnRank:
    try:
        return LpsnRank(c)
    except ValueError as e:
        raise TaxonRankError(f"Failed matching value to LPSN rank: {c}") from e
