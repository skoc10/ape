"""Masking of Kubernetes objects before anything is stored or analysed.

Values are not deleted but replaced by a typed, stable placeholder such as
``<secret:key_name:7f3a>``. The suffix is the start of an HMAC of the value
under a tenant-specific key: equal secrets get equal placeholders inside one
tenant (so "the same secret is used twice" stays visible), while the value
itself cannot be recovered and placeholders never match across tenants.

Detectors in this module (task 2.6): ``key_name`` and ``pem``. The remaining
ones (known token formats, connection strings, entropy, e-mail) arrive in 3.2.
"""

import hashlib
import hmac
import re
from collections import Counter
from typing import Any

DETECTORS = ("key_name", "pem")

# Field names whose string values are treated as secrets.
_SENSITIVE_KEY = re.compile(
    r"pass(?:word|wd|phrase)?|secret|token|api[-_.]?key|credential|private[-_.]?key|access[-_.]?key",
    re.IGNORECASE,
)

# Kubernetes fields that match the pattern above but hold references or settings,
# not secret values. Rules and patches need them intact. The exemption covers
# mappings, lists, numbers and booleans only: a plain string under one of these
# names (e.g. an annotation called "secret") is masked like any other, unless
# the key is a name reference listed in _REFERENCE_KEYS.
_STRUCTURAL_KEYS = frozenset(
    {
        "secretname",
        "secretnamespace",
        "secretkeyref",
        "secretref",
        "secret",
        "secrets",
        "imagepullsecrets",
        "serviceaccounttoken",
        "automountserviceaccounttoken",
        "tokenexpirationseconds",
        "tokenrequests",
        "nodepublishsecretref",
        "passwordpolicy",
    }
)

_REFERENCE_KEYS = frozenset({"secretname", "secretnamespace"})

# {"name": "Authorization", "value": "..."}: HTTP headers that carry credentials.
_SENSITIVE_NAMES = frozenset({"authorization", "proxy-authorization", "cookie", "set-cookie"})

_PEM = re.compile(r"-----BEGIN ([A-Z0-9 ]+)-----.*?-----END \1-----", re.DOTALL)

_PLACEHOLDER = re.compile(r"^<(?:secret:[a-z_]+:[0-9a-f]{4}|redacted:len=\d+)>$")


def derive_tenant_key(master_key: bytes, org_id: str) -> bytes:
    """Per-tenant HMAC key, so placeholders cannot be correlated across tenants."""
    return hmac.new(master_key, b"ape-masking:" + org_id.encode(), hashlib.sha256).digest()


def _is_sensitive_key(key: str, value: Any = None) -> bool:
    lowered = key.lower()
    if lowered in _REFERENCE_KEYS:
        return False
    if lowered in _STRUCTURAL_KEYS and not isinstance(value, str):
        return False
    return _SENSITIVE_KEY.search(key) is not None


def _is_sensitive_name(name: Any) -> bool:
    return isinstance(name, str) and (
        name.lower() in _SENSITIVE_NAMES or _is_sensitive_key(name, "")
    )


class Masker:
    """Masks objects for one tenant and counts redactions per detector."""

    def __init__(self, tenant_key: bytes) -> None:
        if len(tenant_key) < 16:
            raise ValueError("masking key is too short")
        self._key = tenant_key
        self.redactions: Counter[str] = Counter()

    def placeholder(self, detector: str, value: str) -> str:
        tag = hmac.new(self._key, value.encode(), hashlib.sha256).hexdigest()[:4]
        self.redactions[detector] += 1
        return f"<secret:{detector}:{tag}>"

    def mask(self, obj: Any) -> Any:
        """Returns a masked deep copy of a JSON-like value."""
        return self._walk(obj, sensitive=False)

    def _walk(self, node: Any, *, sensitive: bool) -> Any:
        if isinstance(node, dict):
            return self._mask_dict(node)
        if isinstance(node, list):
            return [self._walk(item, sensitive=sensitive) for item in node]
        if isinstance(node, str):
            return self._mask_string(node, sensitive=sensitive)
        return node

    def _mask_dict(self, node: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        # {"name": "DB_PASSWORD", "value": "..."}: the env var / parameter shape, where
        # the sensitive word sits in a sibling field instead of the key.
        named_secret = _is_sensitive_name(node.get("name"))
        for key, value in node.items():
            sensitive = _is_sensitive_key(key, value) or (named_secret and key == "value")
            if sensitive and isinstance(value, dict):
                # A nested mapping under a sensitive key (e.g. stringData-like maps): every
                # string below it is a secret value.
                out[key] = self._mask_all_strings(value)
            else:
                out[key] = self._walk(value, sensitive=sensitive)
        return out

    def _mask_all_strings(self, node: Any) -> Any:
        if isinstance(node, dict):
            return {k: self._mask_all_strings(v) for k, v in node.items()}
        if isinstance(node, list):
            return [self._mask_all_strings(v) for v in node]
        if isinstance(node, str):
            return self._mask_string(node, sensitive=True)
        return node

    def _mask_string(self, value: str, *, sensitive: bool) -> str:
        if _PLACEHOLDER.match(value):
            return value  # already masked (by the agent or an earlier pass): stay idempotent
        if sensitive and value:
            return self.placeholder("key_name", value)
        if "-----BEGIN " in value:
            return _PEM.sub(lambda m: self.placeholder("pem", m.group(0)), value)
        return value
