# nomman/presentation/markdown.py

from itertools import chain
from typing import override

from nomman.models import ClassifyReport, TaxonClassification
from nomman.values import NomenStatus

from .base import ReportRenderer


class MdReportRenderer(ReportRenderer):
    """Renders the ClassificationReport as a GitHub-flavored Markdown document."""

    @override
    def render(self, report: ClassifyReport) -> str:
        # Extract all unique systems involved in this run for table columns
        all_systems = sorted({sys for tc in report.results for sys in tc.report_sys}, key=lambda s: s.name)
        system_names = [s.name for s in all_systems]
        # Build report sections
        sections = [
            "# NOMMAN Run Report\n***",
            _render_run_info(report.run_info),
            _render_authorities(report.authorities),
            _render_classification_results(report.results, system_names),
        ]
        return "\n".join(sections)


def _render_run_info(run_info) -> str:
    return (
        f"### Execution Metadata\n"
        f"- **Command line**: `{run_info.command_line}`\n"
        f"- **Started at**: {run_info.timestamp}\n"
        f"- **Platform**: {run_info.platform}\n"
        f"- **Python**: {run_info.python_version}\n"
        f"***"
    )


def _render_authorities(authorities) -> str:
    lines = ["## Authority Providers\n***"]
    for auth in sorted(authorities, key=lambda a: a.rank):
        lines.append(
            f"**{auth.name}** (Rank: {auth.rank})\n"
            f"- Source: `{auth.source_file}`\n"
            f"- Checksum: `{auth.checksum}`\n"
            f"- Summary: {auth.summary}\n"
        )
    lines.append("***")
    return "\n".join(lines)


def _render_classification_results(results, system_names: list[str]) -> str:
    # Split results into matched (valid/invalid) and unmatched
    matched: list[TaxonClassification] = []
    unmatched: list[TaxonClassification] = []
    for res in sorted(results, key=lambda x: x.taxon.value):
        if res.validation.status == NomenStatus.UNMATCHED:
            unmatched.append(res)
        else:
            matched.append(res)
    output = ["## Nomenclature Classification Results\n***"]
    output.append(_build_matched_table(matched, system_names))
    output.append(_build_unmatched_table(unmatched, system_names))
    return "\n".join(output)


def _build_matched_table(matched: list[TaxonClassification], sys_names: list[str]) -> str:
    if not matched:
        return "_No matched taxa found._\n"
    headers = ["Organism Name", *sys_names, "Status", "Reason", "Replacement", "Domain Impact"]
    dividers = ["---"] + [":--:"] * len(sys_names) + ["---"] * 4
    rows = [_build_matched_row(tc, sys_names) for tc in matched]
    return "\n".join(["### Matched Names", f"| {' | '.join(headers)} |", f"| {' | '.join(dividers)} |", *rows, ""])


def _build_matched_row(tc: TaxonClassification, sys_names: list[str]) -> str:
    report_systems = {s.name for s in tc.report_sys}
    sys_presence = ["o" if name in report_systems else " " for name in sys_names]
    # Validation details
    status_mark = "o" if tc.is_adopted else "x"
    replacement = f"*{tc.replacement_name}*" if tc.replacement_name else " "
    # Impact details (formatted as [Domain]: Group)
    impact_str = " "
    if tc.impact.has_impact:
        lost_gen = (f"[- {m.domain.title}: {m.group.name}]" for m in tc.impact.lost)
        gained_gen = (f"[+ {m.domain.title}: {m.group.name}]" for m in tc.impact.gained)
        impact_str = ", ".join(chain(lost_gen, gained_gen))
    row_cells = [
        f"*{tc.taxon}*" + _show_aliases(tc.taxon.aliases),
        *sys_presence,
        status_mark,
        tc.validation.reason,
        replacement,
        impact_str,
    ]
    return f"| {' | '.join(row_cells)} |"


def _build_unmatched_table(unmatched: list[TaxonClassification], sys_names: list[str]) -> str:
    if not unmatched:
        return "_No unmatched taxa found._\n"
    headers = ["Organism Name", *sys_names, "Close matches"]
    dividers = ["---"] + [":--:"] * len(sys_names) + ["---"]
    rows: list[str] = []
    for tc in unmatched:
        present_systems = {s.name for s in tc.report_sys}
        sys_presence = ["o" if sys_name in present_systems else " " for sys_name in sys_names]
        suggestions_str = " "
        if tc.validation.suggestions:
            suggestions_list = [f"*{m.suggestion}* ({m.score:.3f})" for m in tc.validation.suggestions]
            suggestions_str = ", ".join(suggestions_list)
        row = [f"*{tc.taxon}*" + _show_aliases(tc.taxon.aliases), *sys_presence, suggestions_str]
        rows.append(f"| {' | '.join(row)} |")
    return f"### Unmatched Names\n| {' | '.join(headers)} |\n| {' | '.join(dividers)} |\n" + "\n".join(rows) + "\n"


def _show_aliases(aliases: frozenset[str]) -> str:
    return f"<br>(Aliases: {', '.join([f'*{i}*' for i in sorted(aliases)])})" if aliases else ""
