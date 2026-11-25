"""S3 client wrapper.

S3 has no row level security, so isolation lives here: every key starts with
``{org_id}/`` and is built by this module from the tenant context. No function
accepts a ready-made key from a caller, and buckets are private.
"""

import asyncio
import re
from pathlib import Path
from typing import Any
from uuid import UUID

import boto3
from botocore.config import Config

from ape.core.config import Settings

# Better Auth ids are URL-safe; anything else must never become part of a key.
_ORG_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _prefix(org_id: str, run_id: UUID) -> str:
    if not _ORG_ID.fullmatch(org_id):
        raise ValueError("org_id is not usable as an object key prefix")
    return f"{org_id}/{run_id}"


def snapshot_key(org_id: str, run_id: UUID) -> str:
    return f"{_prefix(org_id, run_id)}/snapshot.json.gz"


class ObjectStore:
    def __init__(self, settings: Settings) -> None:
        self._snapshots = settings.s3_bucket_snapshots
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=Config(
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
                # Plain checksums: not every S3-compatible server accepts the newer trailers.
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    async def put_snapshot(self, org_id: str, run_id: UUID, path: Path) -> str:
        """Uploads the masked snapshot and returns its key."""
        key = snapshot_key(org_id, run_id)
        await asyncio.to_thread(
            self._client.upload_file,
            str(path),
            self._snapshots,
            key,
            ExtraArgs={"ContentType": "application/json", "ContentEncoding": "gzip"},
        )
        return key

    async def get_snapshot(self, org_id: str, run_id: UUID) -> bytes:
        """The gzip bytes of a snapshot (used by the worker from week 3 and by tests)."""
        response = await asyncio.to_thread(
            self._client.get_object, Bucket=self._snapshots, Key=snapshot_key(org_id, run_id)
        )
        body: bytes = await asyncio.to_thread(response["Body"].read)
        return body
