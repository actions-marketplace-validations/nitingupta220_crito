"""Typed errors shared by the OpenRouter client and the ensemble.

Stdlib-only (no httpx) on purpose: the ensemble imports these to distinguish
"advance to the next model" from "the key is dead, abort the run" WITHOUT pulling
in httpx, so the stdlib-only smoke test can still import ``crito.ensemble``.
"""


class OpenRouterError(Exception):
    """Base for OpenRouter client errors."""


class ModelUnavailable(OpenRouterError):
    """The requested model could not serve this call (404 dead slug / 403 / 400
    invalid id / 429 or 503 after the retry cap / network error). The caller
    should ADVANCE to the next model in its ranked pool rather than give up."""


class KeyFatal(OpenRouterError):
    """The API key itself is unusable (401 unauthorized / 402 negative balance).
    Trying another model will not help — abort the run."""
