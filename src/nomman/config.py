# config.py
"""Module for config initialization"""

import logging
from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import Any, Final, get_type_hints

import nomman.utils as utils
from nomman.exceptions import ConfigError, ExternalError

logger = logging.getLogger(__name__)

# Mapping of ProgConfig field names to their locations in the TOML file
# If the value is a string, it's section with field name as key; else it's (section, key).
_AUTHS: Final[str] = "auth_providers"
_CONFIG_MAP: Final[MappingProxyType[str, str | tuple[str, str]]] = MappingProxyType(
    {
        "assert_rank": _AUTHS,
        "lpsn_rank": _AUTHS,
        "mycb_rank": _AUTHS,
        "assert_file": _AUTHS,
        "lpsn_file": _AUTHS,
        "mycb_file": _AUTHS,
        "domains_file": "domains",
        "lpsn_user": ("lpsn_api", "username"),
    }
)


@dataclass(frozen=True, slots=True)
class ProgConfig:
    assert_rank: int = -1
    lpsn_rank: int = -1
    mycb_rank: int = -1
    assert_file: str = ""
    lpsn_file: str = ""
    mycb_file: str = ""
    domains_file: str = ""
    lpsn_user: str = ""

    def __post_init__(self) -> None:
        logger.debug("Performing config validation.")
        self._validate_types()
        self._validate_unique()

    def _validate_types(self) -> None:
        for name, exp_t in get_type_hints(self.__class__).items():
            if not isinstance(v := getattr(self, name), exp_t) or (exp_t is int and isinstance(v, bool)):
                raise ConfigError(f"Incorrect type for {name}: expected {exp_t.__name__}, got {type(v).__name__}")

    def _validate_unique(self) -> None:
        groups = {x: [f.name for f in fields(self) if f.name.endswith(f"_{x}")] for x in ("rank", "file")}
        for group_name, field_names in groups.items():
            vals = [getattr(self, name) for name in field_names]
            active_vals = [v for v in vals if (v != "" if group_name == "file" else utils.cast_int(v) >= 0)]
            if len(active_vals) != len(set(active_vals)):
                raise ConfigError(f"Duplicate values detected in config fields for {group_name}")


def _get_nested_val(config_dict: dict[str, Any], section: str, key: str, default: Any) -> tuple[Any, bool]:
    try:
        return config_dict.get(section, {}).get(key, default), True
    except (AttributeError, TypeError):
        logger.warning(f"Config section '{section}' is malformed. Using default for {key}: {default}")
        return default, False


def init_config(cli_path: str | None = None) -> ProgConfig:
    config_path = cli_path or "config.toml"
    try:
        cfg_toml = utils.read_toml_config(config_path)
        logger.debug("Config file read.")
    except OSError as e:
        raise ExternalError("System fails to read config file") from e
    except (ValueError, TypeError, KeyError) as e:
        raise ConfigError("Config file setup error") from e
    config_args: dict[str, Any] = {}
    for field_name, path_info in _CONFIG_MAP.items():
        (section, key) = (path_info, field_name) if isinstance(path_info, str) else path_info
        # default value from dataclass definition as fallback
        default = next((f.default for f in fields(ProgConfig) if f.name == field_name), None)
        val, found = _get_nested_val(cfg_toml, section, key, default)
        if not found:
            logger.warning(f"Config key '{section}.{key}' missing. Using default: {default}")
        config_args[field_name] = val
    config = ProgConfig(**config_args)
    logger.debug(f"<User assert list> set to: {config.assert_file}")
    logger.debug(f"<LPSN offline DB> set to: {config.lpsn_file}")
    return config
