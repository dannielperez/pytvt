"""Custom exceptions for the pytvt package.

Separated from :mod:`pytvt.models` so that low-level modules can raise
specific errors without importing the full model tree.
"""

from __future__ import annotations


class PytvtError(Exception):
    """Base exception for all pytvt errors."""


class BackendError(PytvtError):
    """Raised when a backend scan fails in a way the caller should handle."""

    def __init__(self, message: str, *, backend: str = "") -> None:
        super().__init__(message)
        self.backend = backend


class RegistryError(PytvtError):
    """Raised for backend/integration registry configuration problems."""
