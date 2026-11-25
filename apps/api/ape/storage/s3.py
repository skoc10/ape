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


def _org_prefix(org_id: str) -> str:
    if not _ORG_ID.fullmatch(org_id):
        raise ValueError("org_id is not usable as an object key prefix")
    return org_id


def logo_key(org_id: str) -> str:
    return f"{_org_prefix(org_id)}/branding/logo"


def report_key(org_id: str, run_id: UUID, extension: str) -> str:
    if extension not in {"html", "pdf"}:
        raise ValueError("unknown report format")
    return f"{_prefix(org_id, run_id)}/report.{extension}"


def snapshot_key(org_id: str, run_id: UUID) -> str:
    return f"{_prefix(org_id, run_id)}/snapshot.json.gz"


class ObjectStore:
    def __init__(self, settings: Settings) -> None:
        self._snapshots = settings.s3_bucket_snapshots
        self._reports = settings.s3_bucket_reports
        self._client: Any = self._make_client(settings, settings.s3_endpoint_url)
        # Presigned URLs embed the host they were signed for, so links handed to a
        # browser are signed against the public address.
        self._public_client: Any = (
            self._make_client(settings, settings.s3_public_endpoint_url)
            if settings.s3_public_endpoint_url
            else self._client
        )

    @staticmethod
    def _make_client(settings: Settings, endpoint_url: str) -> Any:
        return boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=Config(
                s3={"addressing_style": "path"},
                # SigV4 everywhere: the legacy signature breaks presigned links that carry
                # response headers on S3-compatible servers.
                signature_version="s3v4",
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

    # --- branding and reports (reports bucket) -------------------------------------------

    async def put_logo(self, org_id: str, data: bytes, content_type: str) -> str:
        key = logo_key(org_id)
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._reports,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return key

    async def get_logo(self, org_id: str) -> tuple[bytes, str] | None:
        try:
            response = await asyncio.to_thread(
                self._client.get_object, Bucket=self._reports, Key=logo_key(org_id)
            )
        except self._client.exceptions.NoSuchKey:
            return None
        body: bytes = await asyncio.to_thread(response["Body"].read)
        return body, str(response.get("ContentType") or "application/octet-stream")

    async def delete_logo(self, org_id: str) -> None:
        await asyncio.to_thread(
            self._client.delete_object, Bucket=self._reports, Key=logo_key(org_id)
        )

    async def put_report(
        self, org_id: str, run_id: UUID, extension: str, data: bytes, content_type: str
    ) -> str:
        key = report_key(org_id, run_id, extension)
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._reports,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return key

    async def get_report(self, org_id: str, run_id: UUID, extension: str) -> bytes | None:
        try:
            response = await asyncio.to_thread(
                self._client.get_object,
                Bucket=self._reports,
                Key=report_key(org_id, run_id, extension),
            )
        except self._client.exceptions.NoSuchKey:
            return None
        body: bytes = await asyncio.to_thread(response["Body"].read)
        return body

    async def presign_report(
        self, org_id: str, run_id: UUID, extension: str, *, filename: str, expires_in: int = 300
    ) -> str:
        """A short-lived download link, created only after the caller's tenant was checked."""
        url: str = await asyncio.to_thread(
            self._public_client.generate_presigned_url,
            "get_object",
            Params={
                "Bucket": self._reports,
                "Key": report_key(org_id, run_id, extension),
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=expires_in,
        )
        return url
