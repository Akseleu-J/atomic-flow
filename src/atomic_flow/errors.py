"""Typed errors surfaced by the CLI and HTTP API."""


class AtomicFlowError(Exception):
    """Base class for expected application errors."""


class ConfigurationError(AtomicFlowError):
    pass


class BackendError(AtomicFlowError):
    pass


class ResponseFormatError(BackendError):
    """The model answered, but not with the JSON object we asked for."""


class ConfirmationRequired(AtomicFlowError):
    pass


class BudgetExceeded(AtomicFlowError):
    pass


class TreeError(AtomicFlowError):
    pass
