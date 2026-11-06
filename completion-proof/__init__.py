"""Completion Proof — native Hermes plugin entry point.

Evidence receipts tied to the actual source/test/artifact revision, not just a
model's claim that tests passed. See README.md.
"""

from .completion_proof.runtime import register

__all__ = ["register"]
