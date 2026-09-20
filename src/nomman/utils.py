# utils.py
"""Module to hold various general utility functions & classes"""

import gzip
import hashlib
import lzma
import tomllib
from pathlib import Path
from typing import TextIO, cast

from charset_normalizer import from_bytes

from nomman.exceptions import ConfigError, EncodingError

ENC = "utf-8-sig"
ENC_CHK_SIZE = 1048576


def is_int(s) -> bool:  # noqa: ANN001
    try:
        int(s)
        return True
    except (ValueError, TypeError):
        return False


def cast_int(s) -> int:  # noqa: ANN001
    try:
        return int(s)
    except (ValueError, TypeError) as e:
        raise RuntimeError(f"Failed conversion to int: {s}") from e


def cast_str(s) -> str:  # noqa: ANN001
    try:
        return str(s) if s is not None else ""
    except ValueError as e:  # pragma: no cover
        raise RuntimeError(f"Failed conversion to str by {cast_str.__qualname__}") from e


def calc_checksum(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def validate_utf8(path_or_stream: str | Path | bytes) -> None:
    """Validates that the input is UTF-8."""
    filepath = None
    if isinstance(path_or_stream, (str, Path)):
        filepath = path_or_stream
        with open(path_or_stream, "rb") as f:
            data = f.read(ENC_CHK_SIZE)
    else:
        data = path_or_stream[:ENC_CHK_SIZE]
    if not data:
        return
    encoding = best.encoding.lower() if (best := from_bytes(data).best()) else "unknown"
    if encoding not in ("ascii", "utf_8", "utf_8_sig"):
        in_file = f" in {filepath}" if filepath else ""
        raise EncodingError(f"File is not UTF-8 encoded. Detected: '{encoding}'{in_file}")


def text_open(path: str, mode: str = "rt", encoding: str = ENC) -> TextIO:
    """Wraps open() with charset validation for text modes."""
    assert "b" not in mode, f"safe_text_open() cannot be used with binary mode '{mode}'"
    if any(char in mode for char in ("r", "a")):
        validate_utf8(path)
    return cast(TextIO, open(path, mode=mode, newline="", encoding=encoding))


def flex_opener(path: Path, mode: str = "rt", encoding: str = ENC) -> TextIO:
    """Opens a file, automatically handling .gz and .xz compression with charset validation."""
    OPENERS = {".gz": gzip.open, ".xz": lzma.open}
    opener_func = OPENERS.get(path.suffix.lower(), open)
    with opener_func(path, mode="rb") as rb:  # Open in binary mode first to validate encoding
        validate_utf8(rb.read(ENC_CHK_SIZE))
    return opener_func(path, mode=mode, newline="", encoding=encoding)


def read_toml_config(fp: str = "config.toml") -> dict:
    try:
        validate_utf8(fp)
        with open(fp, mode="rt", encoding=ENC, newline="") as f:
            t = tomllib.loads(f.read())
    except (OSError, ValueError, EncodingError) as e:
        raise ConfigError(f"Unable to load config from {fp}") from e
    return t
