"""Framing helpers on top of the generated protocol models."""

import os
import time
from datetime import UTC, datetime

from pydantic import BaseModel, ValidationError

from ape.gateway.protocol_gen import (
    CollectChunkPayload,
    CollectDonePayload,
    CollectRequestPayload,
    Envelope,
    ErrorPayload,
    HelloAckPayload,
    HelloPayload,
    MessageType,
    PingPayload,
    PongPayload,
)

MAX_FRAME_BYTES = 512 * 1024

PAYLOADS: dict[MessageType, type[BaseModel]] = {
    MessageType.hello: HelloPayload,
    MessageType.hello_ack: HelloAckPayload,
    MessageType.ping: PingPayload,
    MessageType.pong: PongPayload,
    MessageType.collect_request: CollectRequestPayload,
    MessageType.collect_chunk: CollectChunkPayload,
    MessageType.collect_done: CollectDonePayload,
    MessageType.error: ErrorPayload,
}

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class ProtocolError(Exception):
    """The frame is not a valid protocol message."""


def new_ulid() -> str:
    """48-bit millisecond timestamp + 80 random bits, Crockford base32 (26 chars)."""
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_CROCKFORD[(value >> shift) & 31] for shift in range(125, -1, -5))


def decode(frame: str) -> tuple[Envelope, BaseModel]:
    """Parses and validates one text frame. Raises ProtocolError without echoing its content."""
    if len(frame.encode()) > MAX_FRAME_BYTES:
        raise ProtocolError("frame too large")
    try:
        envelope = Envelope.model_validate_json(frame)
        payload = PAYLOADS[envelope.type].model_validate(envelope.payload)
    except ValidationError as exc:
        # Only the error locations: the offending values may be cluster data.
        where = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise ProtocolError(f"invalid message at {', '.join(where) or 'root'}") from None
    return envelope, payload


def encode(payload: BaseModel) -> str:
    """Wraps a payload model into an envelope and serializes it."""
    message_type = next(t for t, model in PAYLOADS.items() if isinstance(payload, model))
    envelope = Envelope(
        v=1,
        type=message_type,
        id=new_ulid(),
        ts=datetime.now(UTC),
        payload=payload.model_dump(mode="json", exclude_none=True),
    )
    return envelope.model_dump_json()
