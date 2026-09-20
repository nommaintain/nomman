#  nomman/credentials/providers.py
from typing import Protocol, override

from . import key_store


class PwdProvider(Protocol):
    """Interface for retrieving a password."""

    def get_pwd(self, user: str) -> str | None: ...


class KeyringProvider(PwdProvider):
    """Infrastructure provider: fetches from system keyring."""

    @override
    def get_pwd(self, user: str) -> str | None:
        return key_store.get_lpsn_pw(user)


class LazyLpsnPwdProvider(PwdProvider):
    """Tries the keyring first; if empty, falls back to an interactive provider."""

    def __init__(self, interactive_provider: PwdProvider):
        self._keyring = KeyringProvider()
        self._interactive = interactive_provider

    @override
    def get_pwd(self, user: str) -> str | None:
        if pw := self._keyring.get_pwd(user):  # try Keyring first (silent)
            return pw
        return self._interactive.get_pwd(user)  # fallback to interactive (prompt user)
