"""Object storage. Tenant isolation here is enforced by key construction, see s3.py."""

from ape.storage.s3 import ObjectStore, snapshot_key

__all__ = ["ObjectStore", "snapshot_key"]
