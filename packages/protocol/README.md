# protocol

`protocol.schema.json` is the single source of truth for the messages between the
Data Plane agent and the Control Plane (see section 3 of `mvp-plan.md`).

- `task gen` regenerates `agent/internal/protocol/types_gen.go` (go-jsonschema) and
  `apps/api/ape/gateway/protocol_gen.py` (datamodel-code-generator). Both generators are pinned.
- `task gen:check` fails when the committed code is stale; CI runs it.
- `examples/` holds one valid message per type. The Go and the Python test suites both
  decode every example, so the two sides cannot drift apart silently.

Every message is a JSON text frame of at most 512 KiB:

```json
{"v": 1, "type": "hello", "id": "<ULID>", "ts": "<RFC 3339>", "payload": {}}
```

| Direction | `type` | Payload |
| --- | --- | --- |
| agent -> CP | `hello` | `HelloPayload` |
| CP -> agent | `hello_ack` | `HelloAckPayload` |
| both | `ping` / `pong` | `PingPayload` / `PongPayload` |
| CP -> agent | `collect_request` | `CollectRequestPayload` |
| agent -> CP | `collect_chunk` | `CollectChunkPayload` |
| agent -> CP | `collect_done` | `CollectDonePayload` |
| agent -> CP | `error` | `ErrorPayload` |
