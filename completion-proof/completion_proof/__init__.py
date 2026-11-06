"""Completion Proof core package.

The name is intentionally importable on its own so the deterministic engine can
be tested without a Hermes process. Hermes itself loads the parent directory as
a native plugin and calls ``register(ctx)`` from the root ``__init__.py``.
"""

from .version import ENGINE_VERSION, SCHEMA_VERSION, __version__

__all__ = ["__version__", "ENGINE_VERSION", "SCHEMA_VERSION"]
