# Generated from packages/protocol/protocol.schema.json by "task gen". DO NOT EDIT.

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ApeAgentProtocolV1(BaseModel):
    """
    Messages exchanged between the Data Plane agent and the Control Plane over one WebSocket. Every message is a JSON text frame of at most 512 KiB: an Envelope whose payload is the *Payload type selected by `type`. The agent always opens the connection; the Control Plane can only ask for a predefined collection.
    """


class MessageType(StrEnum):
    """
    Discriminator of the payload carried by an envelope.
    """

    hello = "hello"
    hello_ack = "hello_ack"
    ping = "ping"
    pong = "pong"
    collect_request = "collect_request"
    collect_chunk = "collect_chunk"
    collect_done = "collect_done"
    error = "error"


class Envelope(BaseModel):
    """
    Wrapper of every message.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    v: Literal[1] = Field(..., description="Protocol version. Always 1.")
    type: MessageType
    id: str = Field(..., description="ULID of this message.", pattern="^[0-9A-HJKMNP-TV-Z]{26}$")
    ts: AwareDatetime = Field(..., description="Send time, RFC 3339.")
    payload: dict[str, Any] = Field(
        ..., description="One of the *Payload objects, selected by `type`."
    )


class HelloPayload(BaseModel):
    """
    Agent -> CP, first message after the connection opens.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    agent_version: str = Field(..., max_length=64)
    k8s_version: str = Field(..., max_length=64)
    node_count: int = Field(..., ge=0)
    capabilities: list[str] = Field(
        ...,
        description="Optional features of this agent, e.g. `metrics` when metrics.k8s.io is available.",
        max_length=32,
    )


class CollectLimits(BaseModel):
    """
    Upper bounds the agent must respect during a collection.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    max_pods: int = Field(..., ge=1)
    max_compressed_bytes: int = Field(..., ge=1)
    max_frame_bytes: int = Field(..., ge=1)


class HelloAckPayload(BaseModel):
    """
    CP -> agent, answer to hello.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    session_id: str = Field(..., max_length=64)
    heartbeat_interval_seconds: int = Field(..., ge=1)
    limits: CollectLimits


class AgentHealth(BaseModel):
    """
    Agent self-reported health, so the Control Plane never needs the customer's Prometheus.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    last_collect_seconds: float | None = Field(
        None, description="Duration of the last collection, if any.", ge=0.0
    )
    error_count: int | None = Field(None, description="Errors since the agent started.", ge=0)


class PingPayload(BaseModel):
    """
    Either direction, every heartbeat interval. The peer answers with pong; 3 missed intervals drop the connection.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    health: AgentHealth | None = None


class PongPayload(BaseModel):
    """
    Answer to ping.
    """

    model_config = ConfigDict(
        extra="forbid",
    )


class CollectRequestPayload(BaseModel):
    """
    CP -> agent, the only command that exists: collect a fixed set of resource kinds.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    run_id: UUID
    resources: list[str] = Field(
        ...,
        description="Kubernetes kinds to collect, e.g. Pod, Deployment. Unknown kinds are ignored by the agent.",
        max_length=64,
    )
    exclude_namespaces: list[str] = Field(..., max_length=256)


class CollectChunkPayload(BaseModel):
    """
    Agent -> CP, one batch of whole Kubernetes objects of a single kind. An object is never split across chunks, so each chunk can be masked on arrival.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    run_id: UUID
    seq: int = Field(..., description="0-based position of this chunk in the run.", ge=0)
    kind: str = Field(..., max_length=64)
    count: int = Field(..., description="Number of objects in `data`.", ge=0)
    data: str = Field(
        ...,
        description="base64(gzip(JSON array of objects)).",
        json_schema_extra={"contentEncoding": "base64"},
    )


class CollectDonePayload(BaseModel):
    """
    Agent -> CP, end of a collection.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    run_id: UUID
    total_chunks: int = Field(..., ge=0)
    counts: dict[str, int] = Field(..., description="Objects sent per kind.")
    sha256: str = Field(
        ...,
        description="Hex SHA-256 over the gzip bytes of all chunks, concatenated in `seq` order.",
        pattern="^[0-9a-f]{64}$",
    )
    truncated: bool = Field(
        ...,
        description="True when a collection limit was hit and some objects were left out.",
    )


class ErrorCode(StrEnum):
    forbidden = "forbidden"
    timeout = "timeout"
    limit_exceeded = "limit_exceeded"
    bad_request = "bad_request"
    internal = "internal"


class ErrorPayload(BaseModel):
    """
    Agent -> CP, a collection (or the session, when run_id is absent) failed.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    run_id: UUID | None = None
    code: ErrorCode
    message: str = Field(
        ...,
        description="Human readable, must not contain cluster object contents.",
        max_length=512,
    )
