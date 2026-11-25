"""Masking of Kubernetes objects and text before anything is stored or analysed.

Values are not deleted but replaced by a typed, stable placeholder such as
``<secret:aws_key:7f3a>``. The suffix is the start of an HMAC of the value
under a tenant-specific key: equal secrets get equal placeholders inside one
tenant (so "the same secret is used twice" stays visible), while the value
itself cannot be recovered and placeholders never match across tenants.

Five detector families (the ``detector`` label of ape_masking_redactions_total):

* ``known_format``       AWS, GCP, GitHub, Slack, Stripe tokens, JWTs, PEM blocks
* ``connection_string``  the password in ``scheme://user:password@host``
* ``key_name``           values of fields, env vars, flags and headers with a sensitive name
* ``entropy``            long random-looking strings and hex keys that nothing else caught
* ``pii``                e-mail addresses and, optionally, public IP addresses
"""

import hashlib
import hmac
import ipaddress
import math
import re
from collections import Counter
from collections.abc import Callable
from itertools import pairwise
from typing import Any, Final, Literal

DETECTORS = ("known_format", "connection_string", "key_name", "entropy", "pii")

_SENSITIVE_WORD = (
    r"pass(?:word|wd|phrase)?|pwd|secret|token|api[-_.]?key|credential|private[-_.]?key"
    r"|access[-_.]?key"
)

# Field names whose string values are treated as secrets.
_SENSITIVE_KEY = re.compile(_SENSITIVE_WORD, re.IGNORECASE)

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

# Keys that are sensitive only as the whole name: "auth" is the base64 user:password of a
# registry login, while names that merely contain it (auth-type, authorization-mode) are not.
_SENSITIVE_EXACT_KEYS = frozenset({"auth"})

# {"name": "Authorization", "value": "..."}: HTTP headers that carry credentials.
_SENSITIVE_NAMES = frozenset({"authorization", "proxy-authorization", "cookie", "set-cookie"})

# Identifier-like fields: never secrets, and masking them would break the rule engine.
# The entropy detector skips them; every other detector still runs.
_IDENTIFIER_KEYS = frozenset(
    {
        "uid",
        "resourceversion",
        "name",
        "generatename",
        "namespace",
        "kind",
        "apiversion",
        "image",
        "imageid",
        "containerid",
        "nodename",
        "selflink",
        "fieldpath",
        "mountpath",
        "path",
        "key",
        "pod-template-hash",
        "controller-revision-hash",
        "fingerprint",
    }
)

# Fields that hold a digest, a revision or a machine identifier: long hex strings are
# expected there and are not secrets (checksum/config, git commit, machineID, ...).
_HASH_KEY = re.compile(
    r"checksum|hash|digest|sha(?:1|256|512)?$|revision|commit|fingerprint|uuid|uid$|id$",
    re.IGNORECASE,
)

# Which of the last-resort detectors run on a string: all of them, all but the hex one
# (digest-like fields), or none (identifier fields).
_Entropy = Literal["full", "no_hex", "off"]

_PLACEHOLDER = re.compile(r"<(?:secret:[a-z_]+:[0-9a-f]{4}|redacted:len=\d+)>")
_WHOLE_PLACEHOLDER = re.compile(rf"^{_PLACEHOLDER.pattern}$")
_PLACEHOLDER_SPLIT = re.compile(rf"({_PLACEHOLDER.pattern})")

# --- known formats -----------------------------------------------------------------
_PEM = re.compile(r"-----BEGIN ([A-Z0-9 ]+)-----.*?-----END \1-----", re.DOTALL)
_GCP_SERVICE_ACCOUNT = re.compile(r'"type"\s*:\s*"service_account"')
_KNOWN_FORMATS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA)[A-Z0-9]{16}\b")),
    (
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{50,255})\b"),
    ),
    ("slack_token", re.compile(r"\bxox[abeoprs]-[A-Za-z0-9-]{10,}\b")),
    ("stripe_key", re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
)

# --- connection strings --------------------------------------------------------------
# The password runs up to the last "@" of the authority part, so an unescaped "@" inside
# the password does not leave its tail behind.
_URL_PASSWORD = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]*:)([^\s/'\"]+)(?=@)")
# Signatures of pre-signed URLs (Azure SAS, S3, GCS).
_URL_SIGNATURE = re.compile(
    r"(?i)([?&](?:sig|signature|x-amz-signature|x-goog-signature)=)([^&\s'\"]+)"
)

# --- sensitive names inside free text ---------------------------------------------------
_AUTH_SCHEME = re.compile(r"(?i)\b((?:bearer|basic)\s+)([^\s'\"<>,;]{6,})")
_INLINE_ASSIGNMENT = re.compile(
    r"(?i)((?<![A-Za-z])[\w.-]*(?:" + _SENSITIVE_WORD + r")[\w.-]*['\"]?\s*[=:]\s*['\"]?)"
    r"([^\s'\"&|;,<>{}\[\]]+)"
)

# password="correct horse battery": a quoted value is masked up to its closing quote.
_QUOTED_ASSIGNMENT = re.compile(
    r"(?i)((?<![A-Za-z])[\w.-]*(?:" + _SENSITIVE_WORD + r")[\w.-]*['\"]?\s*[=:]\s*(['\"]))"
    r"([^'\"\n]+)(?=\2)"
)

# "auth": "dXNlcjpwYXNz" inside a JSON document held in a string (registry logins).
_JSON_AUTH = re.compile(r"""(?i)(["']auth["']\s*:\s*["'])([^"'\s]+)""")

# --password hunter2 (flag and value separated by white space)
_FLAG_VALUE = re.compile(
    r"(?i)((?<![\w-])-{1,2}[\w.-]*(?:" + _SENSITIVE_WORD + r")[\w.-]*\s+['\"]?)([^\s'\"-][^\s'\"]*)"
)

# ["--password", "hunter2"]: the flag is one list item, its value the next.
_BARE_SENSITIVE_FLAG = re.compile(r"(?i)^-{1,2}[\w.-]*(?:" + _SENSITIVE_WORD + r")[\w.-]*$")

# mysql -pS3cret: the one widespread short flag that carries its value without a space.
_MYSQL_PASSWORD = re.compile(
    r"(?i)(\b(?:mysql|mysqldump|mysqladmin|mariadb|mariadb-dump)\b[^|;&\n]*?\s-p)"
    r"([^\s'\"-][^\s'\"]*)"
)

# --- pii -----------------------------------------------------------------------------------
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# --- entropy ---------------------------------------------------------------------------------
_ENTROPY_CANDIDATE = re.compile(r"[A-Za-z0-9+/_-]{21,}={0,2}")
_ENTROPY_THRESHOLD = 4.0
_SEGMENT_SPLIT = re.compile(r"[-_/+]")
# Hex keys (openssl rand -hex, many SaaS API keys). Shannon entropy cannot tell them from
# digests (both top out at 4.0 bits), so they are matched by shape and exempted by context.
_HEX_CANDIDATE = re.compile(r"(?<![0-9A-Za-z])(?:[0-9a-f]{32,}|[0-9A-F]{32,})(?![0-9A-Za-z])")
_DIGEST_PREFIX = re.compile(r"(?i)(?:sha(?:1|224|256|384|512)|md5)[:=-]$")


def contains_placeholder(text: str) -> bool:
    """True if the text carries a masking placeholder anywhere."""
    return _PLACEHOLDER.search(text) is not None


def derive_tenant_key(master_key: bytes, org_id: str) -> bytes:
    """Per-tenant HMAC key, so placeholders cannot be correlated across tenants."""
    return hmac.new(master_key, b"ape-masking:" + org_id.encode(), hashlib.sha256).digest()


def _is_sensitive_key(key: str, value: Any = None) -> bool:
    lowered = key.lower()
    if lowered in _REFERENCE_KEYS:
        return False
    if lowered in _SENSITIVE_EXACT_KEYS and isinstance(value, str):
        return True
    if lowered in _STRUCTURAL_KEYS and not isinstance(value, str):
        return False
    return _SENSITIVE_KEY.search(key) is not None


def _is_sensitive_name(name: Any) -> bool:
    return isinstance(name, str) and (
        name.lower() in _SENSITIVE_NAMES or _is_sensitive_key(name, "")
    )


def shannon_entropy(text: str) -> float:
    counts = Counter(text)
    total = len(text)
    return -sum(n / total * math.log2(n / total) for n in counts.values())


def _entropy_floor(length: int) -> float:
    """4.0 bits, lowered for short strings: 21 characters cannot exceed 4.39 bits at all,
    and a third of all random 21-character tokens stay below 4.0."""
    return min(_ENTROPY_THRESHOLD, 0.85 * math.log2(length))


def _switches(text: str, predicate: Callable[[str], bool]) -> int:
    return sum(predicate(a) != predicate(b) for a, b in pairwise(text))


def _random_alnum(segment: str) -> bool:
    """A run of letters and digits without separators that no naming convention produces.

    Covers what the mixed-case rule misses: base32 and base36 style keys (one letter
    case plus digits) and random letters without a digit. Words and CamelCase
    identifiers are told apart by how the character classes alternate: digits are
    sprinkled through a random string and capitals make up about half of it.
    """
    lower = any(c.islower() for c in segment)
    upper = any(c.isupper() for c in segment)
    digit = any(c.isdigit() for c in segment)
    entropy = shannon_entropy(segment)
    if lower and upper and digit:
        return entropy > _entropy_floor(len(segment))
    if digit and (lower or upper):
        if all(c in "0123456789abcdefABCDEF" for c in segment):
            return False  # hex has its own detector
        return entropy > 3.5 and _switches(segment, str.isdigit) >= 4
    if lower and upper:
        share = sum(c.isupper() for c in segment) / len(segment)
        return 0.25 <= share <= 0.75 and entropy > _entropy_floor(len(segment))
    return False


def _looks_random(token: str) -> bool:
    """Long random-looking strings.

    Either the whole token mixes upper case, lower case and digits with high entropy,
    or one of its separator-free segments looks random on its own. Kubernetes names
    are lower case words joined by separators and qualify under neither.
    """
    body = token.rstrip("=")
    if (
        any(c.islower() for c in body)
        and any(c.isupper() for c in body)
        and any(c.isdigit() for c in body)
        and shannon_entropy(body) > _entropy_floor(len(body))
    ):
        return True
    return any(
        _random_alnum(segment) for segment in _SEGMENT_SPLIT.split(body) if len(segment) >= 21
    )


def _sub_outside_placeholders(
    pattern: re.Pattern[str], replace: Callable[[re.Match[str]], str], text: str
) -> str:
    """Like ``pattern.sub`` but never looks inside an existing placeholder.

    Placeholders contain words such as "secret" themselves; without this guard a
    later detector (or a second masking pass) would mask the placeholder again.
    """
    if "<" not in text:
        return pattern.sub(replace, text)
    pieces = _PLACEHOLDER_SPLIT.split(text)
    return "".join(
        piece if index % 2 else pattern.sub(replace, piece) for index, piece in enumerate(pieces)
    )


class MaskedPayload:
    """Data that has been through the masker.

    The only way to obtain one is ``Masker.mask_payload``; the function that
    talks to the LLM accepts nothing else, which makes sending unmasked data a
    type error instead of a code-review catch.
    """

    __slots__ = ("_data",)
    _token: Final = object()

    def __init__(self, data: Any, token: object) -> None:
        if token is not MaskedPayload._token:
            raise TypeError("MaskedPayload can only be created by Masker.mask_payload")
        self._data = data

    @property
    def data(self) -> Any:
        return self._data


class Masker:
    """Masks objects for one tenant and counts redactions per detector family."""

    def __init__(self, tenant_key: bytes, *, mask_public_ips: bool = False) -> None:
        if len(tenant_key) < 16:
            raise ValueError("masking key is too short")
        self._key = tenant_key
        self._mask_public_ips = mask_public_ips
        self.redactions: Counter[str] = Counter()

    def placeholder(self, family: str, kind: str, value: str) -> str:
        tag = hmac.new(self._key, value.encode(), hashlib.sha256).hexdigest()[:4]
        self.redactions[family] += 1
        return f"<secret:{kind}:{tag}>"

    # --- public API --------------------------------------------------------------------

    def mask(self, obj: Any) -> Any:
        """Returns a masked deep copy of a JSON-like value."""
        return self._walk(obj, sensitive=False, entropy="full")

    def mask_payload(self, obj: Any) -> MaskedPayload:
        return MaskedPayload(self.mask(obj), MaskedPayload._token)

    def mask_text(self, text: str) -> str:
        """Masks free text such as a log line."""
        return self._mask_text(text, entropy="full")

    # --- structure ---------------------------------------------------------------------

    def _walk(self, node: Any, *, sensitive: bool, entropy: _Entropy) -> Any:
        if isinstance(node, dict):
            return self._mask_dict(node)
        if isinstance(node, list):
            out: list[Any] = []
            after_flag = False
            for item in node:
                # ["--password", "hunter2"]: the value of a sensitive flag is the next item.
                is_value = after_flag and isinstance(item, str) and not item.startswith("-")
                out.append(self._walk(item, sensitive=sensitive or is_value, entropy=entropy))
                after_flag = isinstance(item, str) and bool(_BARE_SENSITIVE_FLAG.match(item))
            return out
        if isinstance(node, str):
            return self._mask_string(node, sensitive=sensitive, entropy=entropy)
        return node

    def _mask_dict(self, node: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        # {"name": "DB_PASSWORD", "value": "..."}: the env var / parameter shape, where
        # the sensitive word sits in a sibling field instead of the key.
        name = node.get("name")
        named_secret = _is_sensitive_name(name)
        named_hash = isinstance(name, str) and _HASH_KEY.search(name) is not None
        for key, value in node.items():
            sensitive = _is_sensitive_key(key, value) or (named_secret and key == "value")
            if sensitive and isinstance(value, dict):
                # A nested mapping under a sensitive key (e.g. stringData-like maps): every
                # string below it is a secret value.
                out[key] = self._mask_all_strings(value)
                continue
            entropy: _Entropy = "full"
            if key.lower() in _IDENTIFIER_KEYS:
                entropy = "off"
            elif _HASH_KEY.search(key) or (named_hash and key == "value"):
                entropy = "no_hex"
            out[key] = self._walk(value, sensitive=sensitive, entropy=entropy)
        return out

    def _mask_all_strings(self, node: Any) -> Any:
        if isinstance(node, dict):
            return {k: self._mask_all_strings(v) for k, v in node.items()}
        if isinstance(node, list):
            return [self._mask_all_strings(v) for v in node]
        if isinstance(node, str):
            return self._mask_string(node, sensitive=True, entropy="full")
        return node

    def _mask_string(self, value: str, *, sensitive: bool, entropy: _Entropy) -> str:
        if not value or _WHOLE_PLACEHOLDER.match(value):
            return value  # already masked (by the agent or an earlier pass): stay idempotent
        if sensitive:
            return self.placeholder("key_name", "key_name", value)
        return self._mask_text(value, entropy=entropy)

    # --- text --------------------------------------------------------------------------

    def _mask_text(self, text: str, *, entropy: _Entropy) -> str:
        if "-----BEGIN " in text:
            text = _PEM.sub(lambda m: self.placeholder("known_format", "pem", m.group(0)), text)
        if (
            "service_account" in text
            and "private_key" in text
            and _GCP_SERVICE_ACCOUNT.search(text)
        ):
            return self.placeholder("known_format", "gcp_service_account", text)
        stages: list[tuple[re.Pattern[str], Callable[[re.Match[str]], str]]] = [
            *(
                (pattern, self._replace_whole("known_format", kind))
                for kind, pattern in _KNOWN_FORMATS
            ),
            (_URL_PASSWORD, self._replace_group("connection_string", "url_password")),
            (_URL_SIGNATURE, self._replace_group("connection_string", "url_signature")),
            (_AUTH_SCHEME, self._replace_group("key_name", "key_name")),
            (_QUOTED_ASSIGNMENT, self._replace_group("key_name", "key_name", value=3)),
            (_INLINE_ASSIGNMENT, self._replace_group("key_name", "key_name")),
            (_JSON_AUTH, self._replace_group("key_name", "key_name")),
            (_FLAG_VALUE, self._replace_group("key_name", "key_name")),
            (_MYSQL_PASSWORD, self._replace_group("key_name", "key_name")),
            (_EMAIL, self._replace_whole("pii", "email")),
        ]
        if self._mask_public_ips:
            stages.append((_IPV4, self._replace_public_ip))
        if entropy == "full":
            stages.append((_HEX_CANDIDATE, self._replace_hex))
        if entropy != "off":
            stages.append((_ENTROPY_CANDIDATE, self._replace_random))
        for pattern, replace in stages:
            text = _sub_outside_placeholders(pattern, replace, text)
        return text

    def _replace_whole(self, family: str, kind: str) -> Callable[[re.Match[str]], str]:
        return lambda match: self.placeholder(family, kind, match.group(0))

    def _replace_group(
        self, family: str, kind: str, *, value: int = 2
    ) -> Callable[[re.Match[str]], str]:
        """Masks one group of the match and keeps the text before it.

        Existing placeholders never get here: ``_sub_outside_placeholders`` cuts them
        out of the text first. Text that merely starts like one is masked as usual.
        """

        def replace(match: re.Match[str]) -> str:
            secret = match.group(value)
            prefix = match.string[match.start() : match.start(value)]
            return prefix + self.placeholder(family, kind, secret)

        return replace

    def _replace_public_ip(self, match: re.Match[str]) -> str:
        try:
            address = ipaddress.ip_address(match.group(0))
        except ValueError:
            return match.group(0)
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_unspecified
        ):
            return match.group(0)
        return self.placeholder("pii", "ip", match.group(0))

    def _replace_hex(self, match: re.Match[str]) -> str:
        token = match.group(0)
        if token.isdigit() or _DIGEST_PREFIX.search(match.string, 0, match.start()):
            return token  # a number, or sha256:<digest>
        return self.placeholder("entropy", "hex", token)

    def _replace_random(self, match: re.Match[str]) -> str:
        token = match.group(0)
        return self.placeholder("entropy", "entropy", token) if _looks_random(token) else token
