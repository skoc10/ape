"""Deterministic secret masking (the Control Plane side, second lock).

No LLM, no randomness: the same input and key always give the same output.
"""

from ape.sanitize.masker import (
    DETECTORS,
    MaskedPayload,
    Masker,
    contains_placeholder,
    derive_tenant_key,
)

__all__ = ["DETECTORS", "MaskedPayload", "Masker", "contains_placeholder", "derive_tenant_key"]
