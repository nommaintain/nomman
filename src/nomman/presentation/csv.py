# nomman/presentation/csv.py
import csv
import io
from typing import override

from nomman.models import ClassifyReport

from .base import ReportRenderer


class CsvReportRenderer(ReportRenderer):
    """Renders ClassificationReport as a CSV document."""

    @override
    def render(self, report: ClassifyReport) -> str:
        output = io.StringIO()
        w = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
        # --- Section 1: Run metadata ---
        w.writerow(("# RUN METADATA",))
        w.writerow(("Command Line", report.run_info.command_line))
        w.writerow(("Timestamp", report.run_info.timestamp))
        w.writerow(("Platform", report.run_info.platform))
        w.writerow(("Python Version", report.run_info.python_version))
        w.writerow(())
        # --- Section 2: Authorities ---
        w.writerow(("# AUTHORITIES USED",))
        w.writerow(["Authority", "Rank", "Source File", "Checksum", "Summary"])
        for auth in sorted(report.authorities, key=lambda a: a.rank):
            w.writerow((auth.name, auth.rank, auth.source_file, auth.checksum, auth.summary))
        w.writerow(())
        # --- Section 3: Classification results ---
        w.writerow(("# CLASSIFICATION RESULTS",))
        headers = [
            "Taxon Name",
            "Aliases",
            "Is Adopted",
            "Status",
            "Reason",
            "Replacement Name",
            "Suggestions",
            "Reported Systems",
            "Impact: Lost Memberships",
            "Impact: Gained Memberships",
        ]
        w.writerow(headers)
        for tc in sorted(report.results, key=lambda r: r.taxon.value):
            # Flatten objects to strings
            aliases = "; ".join(sorted(tc.taxon.aliases))
            systems = "; ".join(sorted(sys.name for sys in tc.report_sys))
            # Impact formatted as "Domain: Group"
            lost_impact = "; ".join(f"{m.domain.title}: {m.group.name}" for m in tc.impact.lost)
            gained_impact = "; ".join(f"{m.domain.title}: {m.group.name}" for m in tc.impact.gained)
            suggestions = "; ".join(f"{m.suggestion} ({m.score:.3f})" for m in tc.validation.suggestions)
            w.writerow(
                (
                    tc.taxon.value,
                    aliases,
                    "Yes" if tc.is_adopted else "No",
                    tc.validation.status.value,
                    tc.validation.reason,
                    tc.validation.replacement.value if tc.validation.replacement else "",
                    suggestions,
                    systems,
                    lost_impact,
                    gained_impact,
                )
            )
        return output.getvalue()
