# nomman/repositories/sys_repo.py
"""
Infrastructure Layer for NOMMAN.
This module implements the SysRepo for gathering run info (of the computer system).
"""

import platform
import shlex
import sys
from datetime import datetime
from importlib import metadata

from nomman.models import RunInfo


class SystemRepo:
    """Captures execution environment metadata, and translates into the RunInfo value object."""

    def get_version(self) -> str:
        """
        Retrieves the package version from distribution metadata.
        Falls back to 'unknown' if the package is not installed.
        """
        try:
            return metadata.version("nomman")
        except metadata.PackageNotFoundError:
            return "unknown"

    def get_run_info(self) -> RunInfo:
        """Gathers metadata about the current execution environment."""
        return RunInfo(
            command_line=_get_command_line(),
            timestamp=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            platform=platform.platform(),
            python_version=f"{platform.python_implementation()} {sys.version}",
        )


def _get_command_line() -> str:
    """Returns the quoted command-line arguments used to start the process."""
    cmd_args = sys.argv if getattr(sys, "frozen", False) else getattr(sys, "orig_argv", sys.argv)
    return " ".join(shlex.quote(a) for a in cmd_args)
