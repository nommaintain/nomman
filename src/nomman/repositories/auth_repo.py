# nomman/repositories/auth_repo.py
"""
Infrastructure Layer for NOMMAN.
Implements AuthorityRepo for access and persistence of nomenclature authority metadata.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable

from nomman.config import ProgConfig
from nomman.exceptions import AuthorityInitError
from nomman.models import Authority


class AuthorityProvider(ABC):
    """Abstract base class for nomenclature authority providers (i.e. ProgConfig -> Authority)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Display name of authority."""
        ...

    @abstractmethod
    def create_authority(self, config: ProgConfig) -> Authority | None:
        """
        Orchestrates loading of DB, matcher, and gateway.
        Returns None if authority is disabled in config.
        """
        ...


class AuthorityRepo:
    """Handles orchestration of loading authorities via providers."""

    def __init__(self, providers: Iterable[AuthorityProvider]) -> None:
        self._providers = list(providers)

    def load_authorities(self, config: ProgConfig) -> frozenset[Authority]:
        authorities: list[Authority] = []
        for provider in self._providers:
            try:
                if authority := provider.create_authority(config):
                    authorities.append(authority)
            except (OSError, ValueError) as e:
                raise AuthorityInitError(f"Failed to initialize authority {provider.name}") from e
        return frozenset(authorities)
