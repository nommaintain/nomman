# nomman/presentation/html.py

import json
import logging
from importlib import resources
from string import Template
from typing import override

from nomman.models import ClassifyReport
from nomman.utils import ENC

from .base import ReportRenderer

logger = logging.getLogger(__name__)

REPORT_TEMPLATE_FILE = "report_template.html"
REPORT_TEMPLATE_PKG = "nomman.presentation"


class HtmlReportRenderer(ReportRenderer):
    """Renders ClassificationReport as a modern self-contained HTML page using Pico CSS and VanJS."""

    def __init__(self):
        # Cache template in memory during initialization
        self._template = Template(_load_template_content(REPORT_TEMPLATE_FILE, REPORT_TEMPLATE_PKG))
        logger.debug(f"HTML template loaded from: {REPORT_TEMPLATE_FILE}")

    @override
    def render(self, report: ClassifyReport) -> str:
        # Build list of unique platforms/systems sorting alphabetically
        all_systems = sorted({sys.name for tc in report.results for sys in tc.report_sys})
        # Map dynamic metadata properties
        run_info = {
            "command_line": report.run_info.command_line,
            "timestamp": report.run_info.timestamp,
            "platform": report.run_info.platform,
            "python_version": report.run_info.python_version,
        }
        authorities = []
        for auth in sorted(report.authorities, key=lambda a: a.rank):
            authorities.append(
                {
                    "name": auth.name,
                    "rank": auth.rank,
                    "source_file": auth.source_file,
                    "checksum": auth.checksum,
                    "summary": auth.summary,
                }
            )
        results = []
        for tc in sorted(report.results, key=lambda r: r.taxon.value):
            results.append(
                {
                    "taxon": tc.taxon.value,
                    "aliases": sorted(tc.taxon.aliases),
                    "validation": {
                        "original_name": tc.validation.original_name.value,
                        "status": tc.validation.status.value,
                        "reason": tc.validation.reason,
                        "replacement": tc.validation.replacement.value if tc.validation.replacement else None,
                        "suggestions": [{"name": s.suggestion, "score": s.score} for s in tc.validation.suggestions],
                    },
                    "is_adopted": tc.is_adopted,
                    "active_systems": [sys.name for sys in tc.report_sys],
                    "impact": {
                        "lost": [{"domain": m.domain.title, "group": m.group.name} for m in tc.impact.lost],
                        "gained": [{"domain": m.domain.title, "group": m.group.name} for m in tc.impact.gained],
                        "has_impact": tc.impact.has_impact,
                    },
                }
            )
        payload = {"run_info": run_info, "authorities": authorities, "all_systems": all_systems, "results": results}
        json_payload = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
        return self._template.safe_substitute(data_payload=json_payload)


def _load_template_content(filename: str, pkg_name: str) -> str:
    try:
        return resources.files(pkg_name).joinpath(filename).read_text(encoding=ENC)
    except FileNotFoundError as e:
        raise FileNotFoundError(f"Report template '{filename}' not found in '{pkg_name}'") from e
