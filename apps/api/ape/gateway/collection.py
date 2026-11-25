"""Receiving one collection: verify, mask on arrival, spool, upload.

Raw agent data only ever exists in memory, one chunk at a time. Each chunk is
masked as soon as it is decoded; what is written to the spool file and to
object storage is the masked copy only.
"""

import base64
import binascii
import gzip
import hashlib
import json
import tempfile
import zlib
from pathlib import Path
from typing import Any
from uuid import UUID

from ape.gateway.protocol_gen import CollectChunkPayload, CollectDonePayload
from ape.sanitize import Masker

# Kinds the agent may send; mirrors agent/internal/collect/resources.go.
ALLOWED_KINDS = frozenset(
    {
        "Pod",
        "Deployment",
        "StatefulSet",
        "DaemonSet",
        "Job",
        "CronJob",
        "ServiceAccount",
        "Role",
        "ClusterRole",
        "RoleBinding",
        "ClusterRoleBinding",
        "Namespace",
        "Service",
        "Ingress",
        "NetworkPolicy",
        "Node",
        "ResourceQuota",
        "LimitRange",
        "PodDisruptionBudget",
        "PodMetrics",
        "NodeMetrics",
    }
)

# Decompression bomb guard: one chunk may not inflate beyond this.
MAX_CHUNK_INFLATED_BYTES = 64 * 1024 * 1024


class CollectionError(Exception):
    """The agent sent something that does not add up; the run fails."""


def _inflate(data: bytes) -> bytes:
    decompressor = zlib.decompressobj(wbits=31)  # gzip container
    try:
        out = decompressor.decompress(data, MAX_CHUNK_INFLATED_BYTES)
    except zlib.error as exc:
        raise CollectionError("chunk is not valid gzip") from exc
    if decompressor.unconsumed_tail or not decompressor.eof:
        raise CollectionError("chunk inflates beyond the allowed size or is truncated")
    return out


class Collection:
    """State of one in-flight collection for a single run."""

    def __init__(self, run_id: UUID, masker: Masker, *, max_compressed_bytes: int) -> None:
        self.run_id = run_id
        self._masker = masker
        self._max_compressed_bytes = max_compressed_bytes
        self._digest = hashlib.sha256()
        self._next_seq = 0
        self._compressed_bytes = 0
        self._counts: dict[str, int] = {}
        # Lives across many chunks, so no context manager: discard() closes and deletes it.
        self._tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115
            prefix="ape-snapshot-", suffix=".json.gz", delete=False
        )
        self.path = Path(self._tmp.name)
        self._out = gzip.GzipFile(fileobj=self._tmp, mode="wb", mtime=0)
        self._out.write(
            b'{"version":1,"run_id":' + json.dumps(str(run_id)).encode() + b',"objects":['
        )
        self._wrote_object = False
        self._closed = False

    def add_chunk(self, chunk: CollectChunkPayload) -> None:
        """Verifies and masks one chunk and appends it to the spool. CPU bound: run in a thread."""
        if chunk.run_id != self.run_id:
            raise CollectionError("chunk belongs to a different run")
        if chunk.seq != self._next_seq:
            raise CollectionError(f"expected chunk {self._next_seq}, got {chunk.seq}")
        if chunk.kind not in ALLOWED_KINDS:
            raise CollectionError("chunk has an unknown kind")
        try:
            compressed = base64.b64decode(chunk.data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise CollectionError("chunk is not valid base64") from exc
        self._compressed_bytes += len(compressed)
        if self._compressed_bytes > self._max_compressed_bytes:
            raise CollectionError("collection exceeds the size limit")
        try:
            objects = json.loads(_inflate(compressed))
        except ValueError as exc:
            raise CollectionError("chunk is not valid JSON") from exc
        if not isinstance(objects, list) or len(objects) != chunk.count:
            raise CollectionError("chunk does not hold the announced number of objects")

        for obj in objects:
            if not isinstance(obj, dict):
                raise CollectionError("chunk holds something that is not an object")
            # The kind is fixed by the chunk, not by whatever the object claims.
            masked: dict[str, Any] = self._masker.mask({**obj, "kind": chunk.kind})
            if self._wrote_object:
                self._out.write(b",")
            self._out.write(json.dumps(masked, separators=(",", ":")).encode())
            self._wrote_object = True

        self._digest.update(compressed)
        self._counts[chunk.kind] = self._counts.get(chunk.kind, 0) + chunk.count
        self._next_seq += 1

    def finish(self, done: CollectDonePayload) -> dict[str, Any]:
        """Checks collect_done against what arrived and closes the spool. Returns the run stats."""
        if done.run_id != self.run_id:
            raise CollectionError("collect_done belongs to a different run")
        if done.total_chunks != self._next_seq:
            raise CollectionError(
                f"agent announced {done.total_chunks} chunks, received {self._next_seq}"
            )
        if done.sha256 != self._digest.hexdigest():
            raise CollectionError("checksum mismatch")
        announced = {kind: count for kind, count in done.counts.items() if count}
        if announced != {kind: count for kind, count in self._counts.items() if count}:
            raise CollectionError("object counts do not match")
        self._out.write(b"]}")
        self._close_files()
        return {
            "counts": dict(sorted(self._counts.items())),
            "objects": sum(self._counts.values()),
            "chunks": self._next_seq,
            "compressed_bytes": self._compressed_bytes,
            "truncated": done.truncated,
            "redactions": dict(sorted(self._masker.redactions.items())),
        }

    def _close_files(self) -> None:
        if not self._closed:
            self._closed = True
            self._out.close()
            self._tmp.close()

    def discard(self) -> None:
        """Closes and deletes the spool file. Safe to call more than once."""
        self._close_files()
        self.path.unlink(missing_ok=True)
