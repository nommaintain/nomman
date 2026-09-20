# nomman/repositories/labname_repo.py
"""
Infrastructure Layer for NOMMAN.
This module implements the LabRepo for loading and parsing of laboratory organism names and system inventory.
"""

import csv
import logging
from collections import Counter, defaultdict
from collections.abc import Iterable

from nomman.exceptions import ExternalError, LabInventoryError
from nomman.models import LabInventory, LabSystem
from nomman.utils import text_open
from nomman.values import TaxonName

logger = logging.getLogger(__name__)


class LabRepo:
    """Handles loading and parsing of laboratory organism names (from single input files or CSV file-lists)."""

    def load_inventory(self, input_path: str | None, filelist_path: str | None = None) -> LabInventory:
        """Parses input sources to construct a LabInventory aggregate."""
        _validate_inputs(input_path, filelist_path)
        try:
            if input_path:
                _systems = [_parse_single_file(input_path)]
            elif filelist_path:
                _systems = [
                    LabSystem(name=sys_name, reported_names=names)
                    for sys_name, names in _parse_file_list(filelist_path).items()
                ]
            else:  # should never arrive here
                raise RuntimeError("Fatal program flow: no input specified to create LabInventory")
            _validate_inventory_integrity(inventory := LabInventory(systems=frozenset(_systems)))
            return inventory
        except OSError as e:
            raise ExternalError("Lab organisms: input file(s)/filelist cannot be read.") from e


def _validate_inventory_integrity(inventory: LabInventory) -> None:
    """Checks alias consistency and collisions."""
    all_taxa: frozenset[TaxonName] = frozenset().union(*(sys.reported_names for sys in inventory.systems))
    # 1. Cross-system consistency check (same official name must have same aliases)
    unique_officials = set(official_list := [t.value for t in all_taxa])
    if len(all_taxa) != len(unique_officials):
        violations = [name for name, count in Counter(official_list).items() if count > 1]
        raise LabInventoryError(
            f"Inconsistent aliases across systems for these official names: {', '.join(sorted(violations))}"
        )
    # 2. Alias-to-alias collision check
    unique_aliases = set(all_aliases := [a for t in all_taxa for a in t.aliases])
    if len(all_aliases) != len(unique_aliases):
        alias_map: dict[str, set[str]] = defaultdict(set)
        for t in all_taxa:
            for a in t.aliases:
                alias_map[a].add(t.value)
        violations = [
            f"'{alias}' is assigned to multiple taxa: {sorted(list(officials))}"
            for alias, officials in alias_map.items()
            if len(officials) > 1
        ]
        raise LabInventoryError("Alias collisions found.\n" + "\n".join(sorted(violations)))
    # 3. Alias-to-official collision check (alias may not be an official name)
    if collisions := unique_officials.intersection(unique_aliases):
        raise LabInventoryError(
            f"Nomenclature collision: the following names are used as both official names and aliases: "
            f"{', '.join(sorted(collisions))}."
        )


def _validate_inputs(input_path: str | None, filelist_path: str | None) -> None:
    """Ensures that either a single file or a filelist is provided, but not both; raises ValueError otherwise."""
    if bool(input_path) == bool(filelist_path):
        raise LabInventoryError("Lab organisms: provide single file or filelist but not both!")


def _parse_single_file(path: str) -> LabSystem:
    """Helper for creating a LabSystem from a single file."""
    with text_open(path) as f:
        names = _process_taxon_stream(f, f"file {path}")
    return LabSystem(name="Lab", reported_names=names)


def _parse_file_list(path: str) -> dict[str, frozenset[TaxonName]]:
    """Helper to read a CSV list (in path) and maps systems to their respective sets of organism names."""
    labnames_files: dict[str, str] = {}
    seen_files: set[str] = set()
    with text_open(path) as f:
        for r_idx, row in enumerate(csv.reader(f), start=1):
            if len(row) < 2:
                raise LabInventoryError(f"Filelist line {r_idx}: must have at least 2 columns.")
            sys_name, file_path = row[0].strip(), row[1].strip()
            if not sys_name or not file_path:
                raise LabInventoryError(f"Filelist row {r_idx}: blank key/value found.")
            if sys_name in labnames_files:
                raise LabInventoryError(f"Filelist row {r_idx}: duplicate key (sys name) {sys_name}")
            if file_path in seen_files:
                raise LabInventoryError(f"Filelist row {r_idx}: duplicate value (file path) {file_path}")
            labnames_files[sys_name] = file_path
            seen_files.add(file_path)
    # Resolve files into TaxonNames by opening them and passing to the stream parser
    results: dict[str, frozenset[TaxonName]] = {}
    for sys_name, f_path in labnames_files.items():
        with text_open(f_path) as f:
            results[sys_name] = _process_taxon_stream(f, f"system '{sys_name}' (file {f_path})")
    return results


def _process_taxon_stream(stream: Iterable[str], source_label: str) -> frozenset[TaxonName]:
    """Processes stream of raw lines into a set of TaxonName objects."""
    seen_in_system: dict[str, frozenset[str]] = {}
    for line in stream:
        if not (stripped := line.strip()) or stripped.startswith("#"):
            continue
        if not (official := (parts := stripped.split("%", 1))[0].strip()):
            raise LabInventoryError(f"Malformed name entry (missing official name) in {source_label}: {stripped}")
        if official in seen_in_system:
            logger.warning(f"Duplicate entry for taxon '{official}' found in {source_label}. Ignoring latter entry.")
            continue
        aliases = []
        if len(parts) > 1:
            aliases = [a.strip() for a in parts[1].split("|") if a.strip()]  # Aliases are separated by "|" after %
        seen_in_system[official] = frozenset(aliases)
    return frozenset(TaxonName(value=name, aliases=_aliases) for name, _aliases in seen_in_system.items())
