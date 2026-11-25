"""Agent tokens: generated once, shown once, stored only as a SHA-256 digest."""

import hashlib
import secrets
import string
from dataclasses import dataclass

TOKEN_PREFIX = "ape_"  # noqa: S105 (a public prefix, not a secret)
_ALPHABET = string.digits + string.ascii_uppercase + string.ascii_lowercase  # base62
_DISPLAY_CHARS = 8


@dataclass(frozen=True, slots=True)
class NewAgentToken:
    token: str  # the secret; never stored, never logged
    prefix: str  # "ape_" + first 8 characters, safe to display
    digest: bytes  # sha256(token), the only thing the database keeps


def _base62(data: bytes) -> str:
    number = int.from_bytes(data, "big")
    chars: list[str] = []
    while number:
        number, remainder = divmod(number, 62)
        chars.append(_ALPHABET[remainder])
    return "".join(reversed(chars)) or "0"


def hash_agent_token(token: str) -> bytes:
    # A plain SHA-256 is enough: the token is 256 bits of randomness, not a password.
    return hashlib.sha256(token.encode()).digest()


def generate_agent_token() -> NewAgentToken:
    body = _base62(secrets.token_bytes(32)).rjust(43, "0")
    token = TOKEN_PREFIX + body
    return NewAgentToken(
        token=token,
        prefix=TOKEN_PREFIX + body[:_DISPLAY_CHARS],
        digest=hash_agent_token(token),
    )
