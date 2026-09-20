# nomman/repositories/domain_repo.py
"""
Infrastructure Layer for NOMMAN.
Implements DomainRepo for the loading and orchestration of domain definitions.
"""

import logging
from collections import Counter
from collections.abc import Iterable

from nomman.exceptions import AuthorityFormatError, ExternalError
from nomman.models import Domain
from nomman.services.taxon_resolver import TaxonResolver
from nomman.utils import text_open

logger = logging.getLogger(__name__)


class DomainRepo:
    """Handles loading of domain definitions from file."""

    def __init__(self, resolver: TaxonResolver) -> None:
        self._resolver = resolver

    def load_domains(self, domains_file: str) -> frozenset[Domain]:
        """Loads the domain definition file and resolves its contents."""
        if not domains_file:
            logger.info("No domain definitions file provided. Skipping domain impact analysis.")
            return frozenset()
        try:
            with text_open(domains_file) as f:
                raw_domains = _parse_domain_content(f)
        except OSError as e:
            raise ExternalError(f"Failed to read domain definitions file: {domains_file}") from e
        domains_list = []
        for section, entries in raw_domains.items():
            groups = []
            for entry in entries:
                groups.append(self._resolver.resolve_entry(entry))
            # Domain-level validation: No duplicate group names per domain
            group_names = [g.name for g in groups]
            if len(group_names) != len(set(group_names)):
                dups = [n for n, c in Counter(group_names).items() if c > 1]
                raise ValueError(f'Domain "{section}" contains duplicate entries: {dups}')
            domains_list.append(Domain(title=section, groups=frozenset(groups)))
        logger.info(f"Successfully loaded {len(domains_list)} domains.")
        return frozenset(domains_list)


def _parse_domain_content(lines: Iterable[str]) -> dict[str, list[str]]:
    """Pure logic: processes a stream of lines into a dictionary of sections."""
    domains_dict: dict[str, list[str]] = {}
    current_section = None
    for raw_line in lines:
        if not (line := raw_line.strip()) or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            if not (section_name := line[1:-1].strip()):
                raise AuthorityFormatError('Empty section header "[]" in domain definitions.')
            if section_name in domains_dict:
                raise AuthorityFormatError(f"Duplicate sections in domain definitions: {section_name}")
            current_section = section_name
            domains_dict[current_section] = []
        elif current_section is None:
            logger.warning(f"Entries before first domain section are ignored: {line}")
        else:
            domains_dict[current_section].append(line)
    return domains_dict
