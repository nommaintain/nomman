# nomman/services/cli_handler.py
"""Module to hold various CLI-associated functions & classes"""

import argparse
import getpass
from dataclasses import asdict, dataclass
from typing import Any, Final, Literal, override

from nomman.credentials import key_store, providers
from nomman.exceptions import NommanError
from nomman.taxon_interface import FuzzyConfirmation
from nomman.values import ReportFormat, TaxonName

from .core import FuzzyMatch


@dataclass(frozen=True, slots=True)
class ArgDef:
    flags: tuple[str, ...]
    help: str | None = None
    action: Literal["store_true"] | None = None
    default: Any = None
    choices: tuple | None = None
    nargs: str | int | None = None
    const: Any = None

    def to_kwargs(self) -> dict[str, Any]:
        """Convert dataclass fields to argparse keyword arguments, filtering out None."""
        # exclude 'flags' as they are passed as positional arguments
        return {k: v for k, v in asdict(self).items() if k != "flags" and v is not None}


@dataclass(frozen=True, slots=True)
class CliSchema:
    exclusive_groups: tuple[tuple[ArgDef, ...], ...]
    standard: tuple[ArgDef, ...]


ARG_SCHEMA: Final = CliSchema(
    exclusive_groups=(
        (  # Input
            ArgDef(("-i", "--input"), help="single file input (for single list of lab organism names)"),
            ArgDef(("-l", "--filelist"), help="filelist input (for multiple lists of lab organism names)"),
        ),
        (  # Verbosity
            ArgDef(("-v", "--verbose"), action="store_true", help="enable verbose mode"),
            ArgDef(("-b", "--brief"), action="store_true", help="enable brief (less verbose) mode"),
        ),
        (  # Credentials
            ArgDef(("--store_lpsn_pw",), action="store_true", help="stores LPSN API password and exit"),
            ArgDef(("--clear_lpsn_pw",), action="store_true", help="clears LPSN API password and exit"),
            ArgDef(("-t", "--test_lpsn_query"), help="standalone LPSN query; overrides other operations"),
        ),
    ),
    standard=(
        ArgDef(("-g", "--nomengroups"), help="nomenclature groups (domains) definitions input"),
        ArgDef(("-c", "--config"), help="path to the TOML configuration file", default=None),
        ArgDef(
            ("-f", "--format"),
            choices=tuple(ReportFormat),
            default=ReportFormat.MARKDOWN,
            help="output format of the report (default: md [Markdown])",
        ),
        ArgDef(
            ("--normal",),
            action="store_true",
            help="performs normalization on lab names to auto-correct common nomenclature deviations",
        ),
        ArgDef(
            ("-z", "--fuzzy"),
            nargs="?",
            const=True,
            help="turns on fuzzy match pre-validation (default: off; optionally specify no. of workers)",
        ),
        ArgDef(("-o", "--output"), help="path to save the report file (if omitted, output to stdout)"),
        ArgDef(("--version",), action="store_true", help="show program version and exit"),
    ),
)


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parses command line arguments based on the strongly-typed ARG_SCHEMA."""
    parser = argparse.ArgumentParser(
        prog="nomman_cli",
        description="CLI program to facilitate taxonomic nomenclature management in the clinical microbiology lab.",
    )
    for items in ARG_SCHEMA.exclusive_groups:
        group = parser.add_mutually_exclusive_group()
        for arg in items:
            group.add_argument(*arg.flags, **arg.to_kwargs())
    for arg in ARG_SCHEMA.standard:
        parser.add_argument(*arg.flags, **arg.to_kwargs())
    return parser.parse_args(args)


def parse_fuzzy(a: argparse.Namespace) -> tuple[bool, int | None]:
    """Parses the value of the -z/--fuzzy flag as user limit for max fuzzy match workers"""
    if a.fuzzy:
        fuzzy_enabled, fuzzy_workers = True, None
        if isinstance(a.fuzzy, str) and (not a.fuzzy.isdigit() or (fuzzy_workers := int(a.fuzzy)) < 1):
            raise NommanError(f"Invalid value for --fuzzy. Expected a positive integer, got '{a.fuzzy}'")
        return fuzzy_enabled, fuzzy_workers
    return False, None


class CliPwdProvider(providers.PwdProvider):
    """UI Provider: performs actual terminal prompts."""

    @override
    def get_pwd(self, user: str) -> str | None:
        if not (pw := getpass.getpass(f"No LPSN password saved. Enter password for <{user}>: ")):
            pw = getpass.getpass("No password entered. Enter password to proceed or press Enter to exit: ")
        return pw or None


class CliAdminHandler:
    """Handles administrative password management via CLI."""

    @staticmethod
    def store_password(user: str) -> None:
        print("Entering credentials management mode ...\nUser request saving password to system keyring")
        if not (pw := getpass.getpass(f"Enter LPSN password for user <{user}>: ")):
            print("No password entered. Operation aborted.")
            return
        if pw != getpass.getpass("Enter password again for confirmation: "):
            print("Repeat password does not match first password. Operation aborted.")
            return
        key_store.save_lpsn_pw(user, pw)
        print("Successfully added password to keyring.")

    @staticmethod
    def delete_password(user: str) -> None:
        print("Entering credentials management mode ...\nUser request deleting password from system keyring")
        confirm = input(f"Confirm deletion of stored LPSN password by entering username [{user}]: ")
        if confirm != user:
            print("User did not enter correct username. Operation aborted.")
            return
        key_store.del_lpsn_pw(user)
        print("Successfully deleted password from keyring.")


class CliFuzzyHandler(FuzzyConfirmation):
    """UI Provider: CLI user confirmation for fuzzy match replacements."""

    @override
    def confirm_replace(self, suggestions: dict[TaxonName, tuple[FuzzyMatch, ...]]) -> bool:
        if not (n := len(suggestions)):
            return False
        print("\n--- Fuzzy Match pre-validation ---")
        print(f"{'Name':<30} | {'Best alternative':<30} | {'Score':<6}")
        print("-" * 70)
        for original, matches in sorted(suggestions.items(), key=lambda x: x[0].value):
            best = matches[0]
            print(f"{original.value:<30} | {best.suggestion:<30} | {best.score:.3f}")
        print("-" * 70)
        return input(f"\nFound matches in {n} names. Replace all? [y/N]: ").strip().lower() in ("y", "yes")
