"""Errors specific to M8. Model garbage is handled in code and never becomes a 500."""

from common.errors import UpstreamError


class ModelOutputRejected(UpstreamError):
    """The model kept returning output that fails validation (single-output agents only)."""
