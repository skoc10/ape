"""Object storage. Tenant isolation here is enforced by key construction, see s3.py."""

from ape.storage.s3 import ObjectStore, logo_key, report_key, snapshot_key

__all__ = ["ObjectStore", "logo_key", "report_key", "snapshot_key"]
