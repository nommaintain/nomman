#  nomman/credentials/key_store.py
"""Module holding functions related to credentials management"""

import keyring
import keyring.errors

LPSN_KEYRING_NAME = "nomman-lpsn"


def save_lpsn_pw(user: str, pw: str) -> None:
    try:
        keyring.set_password(LPSN_KEYRING_NAME, user, pw)
    except keyring.errors.KeyringError as e:
        raise RuntimeError("Failed to save password to system keyring") from e


def del_lpsn_pw(user: str) -> None:
    try:
        keyring.delete_password(LPSN_KEYRING_NAME, user)
    except keyring.errors.KeyringError as e:
        raise RuntimeError("Failed to delete password from system keyring") from e


def get_lpsn_pw(user: str) -> str | None:
    return keyring.get_password(LPSN_KEYRING_NAME, user)
