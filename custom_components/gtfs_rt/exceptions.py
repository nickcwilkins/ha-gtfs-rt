"""Exceptions for GTFS API calls.

These exceptions exist to provide common exceptions for the async and sync client libraries.
"""

from homeassistant.exceptions import HomeAssistantError

class GtfsApiError(HomeAssistantError):
    """Base class for GTFS API exceptions."""

class GtfsAuthError(GtfsApiError):
    """Exception raised when authentication fails."""