import json
import re
from pathlib import Path

import pytest

from ape.gateway import protocol
from ape.gateway.protocol_gen import HelloAckPayload, MessageType, PongPayload

EXAMPLES = Path(__file__).resolve().parents[3] / "packages" / "protocol" / "examples"


def test_there_is_an_example_for_every_message_type() -> None:
    assert {p.stem for p in EXAMPLES.glob("*.json")} == {t.value for t in MessageType}
    assert set(protocol.PAYLOADS) == set(MessageType)


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.json")), ids=lambda p: p.stem)
def test_examples_decode(path: Path) -> None:
    envelope, payload = protocol.decode(path.read_text())
    assert envelope.type.value == path.stem
    assert isinstance(payload, protocol.PAYLOADS[envelope.type])


def test_encode_round_trips() -> None:
    frame = protocol.encode(
        HelloAckPayload.model_validate(
            json.loads((EXAMPLES / "hello_ack.json").read_text())["payload"]
        )
    )
    envelope, payload = protocol.decode(frame)
    assert envelope.type is MessageType.hello_ack
    assert isinstance(payload, HelloAckPayload)
    assert payload.limits.max_pods == 5000
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", envelope.id)
    assert protocol.decode(protocol.encode(PongPayload()))[0].type is MessageType.pong


def test_ulids_are_unique_and_sortable_by_time() -> None:
    ids = [protocol.new_ulid() for _ in range(500)]
    assert len(set(ids)) == 500
    assert ids[0][:8] <= ids[-1][:8]


@pytest.mark.parametrize(
    "frame",
    [
        "not json",
        "[]",
        '{"v":2,"type":"pong","id":"01JDKX5N8Q4V7YH2M3R6T9WZA0","ts":"2026-11-25T05:00:00Z","payload":{}}',
        '{"v":1,"type":"exec","id":"01JDKX5N8Q4V7YH2M3R6T9WZA0","ts":"2026-11-25T05:00:00Z","payload":{}}',
        '{"v":1,"type":"pong","id":"short","ts":"2026-11-25T05:00:00Z","payload":{}}',
        '{"v":1,"type":"pong","id":"01JDKX5N8Q4V7YH2M3R6T9WZA0","ts":"2026-11-25T05:00:00Z","payload":{"x":1}}',
        '{"v":1,"type":"hello","id":"01JDKX5N8Q4V7YH2M3R6T9WZA0","ts":"2026-11-25T05:00:00Z","payload":{}}',
        '{"v":1,"type":"pong","id":"01JDKX5N8Q4V7YH2M3R6T9WZA0","ts":"2026-11-25T05:00:00Z","payload":{},"org_id":"x"}',
    ],
)
def test_invalid_frames_are_rejected(frame: str) -> None:
    with pytest.raises(protocol.ProtocolError):
        protocol.decode(frame)


def test_oversized_frames_are_rejected() -> None:
    with pytest.raises(protocol.ProtocolError, match="too large"):
        protocol.decode(" " * (protocol.MAX_FRAME_BYTES + 1))


def test_errors_never_echo_the_payload() -> None:
    frame = (
        '{"v":1,"type":"hello","id":"01JDKX5N8Q4V7YH2M3R6T9WZA0","ts":"2026-11-25T05:00:00Z",'
        '"payload":{"agent_version":"hunter2-secret","k8s_version":1,"node_count":-1,"capabilities":[]}}'
    )
    with pytest.raises(protocol.ProtocolError) as excinfo:
        protocol.decode(frame)
    assert "hunter2" not in str(excinfo.value)
    assert "k8s_version" in str(excinfo.value)
