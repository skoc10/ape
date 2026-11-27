"""The audited objects of a run, looked up by identity.

Fixes for Helm releases need the labels of the object a finding is about; they are in the
stored (masked) snapshot, not in the finding.
"""

from typing import Any
from uuid import UUID

import structlog

from ape.analysis.analyst import _index
from ape.analysis.pipeline import _load_objects
from ape.storage import ObjectStore

log = structlog.get_logger(__name__)

ObjectIndex = dict[tuple[str, str | None, str], dict[str, Any]]


async def load_object_index(store: ObjectStore, org_id: str, run_id: UUID) -> ObjectIndex:
    """Empty when the snapshot is gone or unreadable: fixes then work as they do without
    Helm awareness, which is a smaller result but never a wrong one."""
    try:
        return _index(_load_objects(await store.get_snapshot(org_id, run_id)))
    except Exception as exc:
        log.warning("snapshot_unavailable", run_id=str(run_id), error_type=type(exc).__name__)
        return {}
