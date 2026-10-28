"""
Domain exceptions. Each carries an HTTP status code so the REST layer can turn
them into clean JSON errors, and the WebSocket layer can forward the message.
"""

from __future__ import annotations


class DataBridgeError(Exception):
    """Base class for all expected, user-facing errors."""

    status_code: int = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidRequestError(DataBridgeError):
    status_code = 400


class ProviderNotFoundError(DataBridgeError):
    status_code = 404


class SymbolNotFoundError(DataBridgeError):
    status_code = 404


class ProviderUnavailableError(DataBridgeError):
    """The provider exists but is not connected (terminal closed, no internet...)."""

    status_code = 503


class ProviderConnectionError(ProviderUnavailableError):
    """Raised while trying to (re)connect to a data source."""
