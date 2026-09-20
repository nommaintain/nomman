# nomman/presentation/base.py

from typing import Protocol

from nomman.models import ClassifyReport


class ReportRenderer(Protocol):
    """
    Protocol for report renderers.
    Allows the application to support multiple output formats (Markdown, HTML, etc.)
    """

    def render(self, report: ClassifyReport) -> str:
        """Transforms the aggregate report into a formatted string."""
        ...
