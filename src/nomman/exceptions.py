# exceptions.py
"""
Domain-Driven Exceptions for NOMMAN.
Defines a hierarchical structure to categorize failures by architectural layer.
"""


class NommanError(Exception):
    """Base exception for all NOMMAN-related errors."""


class ConfigError(NommanError):
    """Raised when there is an issue with the application configuration."""


# --- Authority Layer ---
class AuthorityError(NommanError):
    """Base class for authority provider failures."""


class AuthorityFormatError(AuthorityError):
    """Raised when an authority data file is malformed."""


class AuthorityInitError(AuthorityError):
    """Raised when an authority cannot be loaded or initialized."""


# --- Taxonomy Layer ---
class TaxonomyError(NommanError):
    """Base class for taxonomic failures."""


class TaxonRankError(TaxonomyError):
    """Raised when an invalid taxonomic rank is encountered."""


class TaxonAmbiguityError(TaxonomyError):
    """Raised when a name matches multiple conflicting taxa."""


class TaxonResolutionError(TaxonomyError):
    """Raised when a name cannot be resolved to a known taxon."""


# --- Lab Inventory Layer ---
class LabInventoryError(NommanError):
    """Raised when laboratory input data is problematic."""


# --- Infrastructure Layer ---
class ExternalError(NommanError):
    """Raised when low-level system or external dependencies fail."""


class EncodingError(ExternalError):
    """Raised when a file is not encoded in the required UTF-8 format."""


class ExternalApiError(ExternalError):
    """Raised when the LPSN API returns an unexpected error."""
