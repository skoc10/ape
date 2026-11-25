"""White-label settings (task 4.1)."""

import struct
import zlib
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from ape.reports.logo import acceptable, dimensions

from .conftest import Tenant, TokenFactory, bearer


def png(width: int = 2, height: int = 2) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    raw = b"".join(b"\x00" + b"\x1f\x6f\xeb" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def png_header_only(width: int, height: int) -> bytes:
    """A PNG that declares a size; the limit must apply before anything is decoded."""
    header = struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + header
        + struct.pack(">I", zlib.crc32(b"IHDR" + header))
    )


def jpeg_header_only(width: int, height: int) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    frame = b"\xff\xc2" + struct.pack(">HBHHB", 17, 8, height, width, 3) + b"\x00" * 9
    return b"\xff\xd8" + app0 + frame


def test_logo_size_is_read_from_the_header() -> None:
    assert dimensions(png(3, 5)) == (3, 5)
    assert dimensions(jpeg_header_only(640, 480)) == (640, 480)
    assert acceptable(png(2048, 1)) and not acceptable(png_header_only(2049, 1))
    assert acceptable(jpeg_header_only(640, 480))
    assert dimensions(b"\xff\xd8\xff\xda\x00\x02") is None  # image data without a frame
    assert dimensions(b"GIF89a") is None


@pytest.fixture
def auth_a(make_token: TokenFactory, tenant_a: Tenant) -> dict[str, str]:
    return bearer(make_token(tenant_a.user_id, tenant_a.org_id))


@pytest.fixture
def auth_b(make_token: TokenFactory, tenant_b: Tenant) -> dict[str, str]:
    return bearer(make_token(tenant_b.user_id, tenant_b.org_id))


def test_branding_is_saved_per_tenant(
    client: TestClient,
    auth_a: dict[str, str],
    auth_b: dict[str, str],
    tenant_a: Tenant,
    admin_db: psycopg.Connection[Any],
) -> None:
    empty = client.get("/v1/settings", headers=auth_a).json()
    assert empty["branding"] == {"company_name": None, "primary_color": None, "has_logo": False}
    assert empty["can_manage"] is True

    saved = client.put(
        "/v1/settings/branding",
        json={"company_name": "  Acme DevOps  ", "primary_color": "#1F6FEB"},
        headers=auth_a,
    )
    assert saved.status_code == 200
    assert saved.json()["branding"] == {
        "company_name": "Acme DevOps",
        "primary_color": "#1f6feb",
        "has_logo": False,
    }
    assert client.get("/v1/settings", headers=auth_b).json()["branding"]["company_name"] is None

    row = admin_db.execute(
        "select branding from organization_settings where org_id = %s", (tenant_a.org_id,)
    ).fetchone()
    assert row == ({"company_name": "Acme DevOps", "primary_color": "#1f6feb"},)

    cleared = client.put(
        "/v1/settings/branding", json={"company_name": "Acme DevOps"}, headers=auth_a
    ).json()
    assert cleared["branding"]["primary_color"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"primary_color": "red"},
        {"primary_color": "#12345"},
        {"primary_color": "#1f6feb; background: url(https://evil.example)"},
        {"company_name": "x" * 81},
        {"company_name": "Acme\nInjected"},
    ],
)
def test_invalid_branding_is_rejected(
    client: TestClient, auth_a: dict[str, str], body: dict[str, str]
) -> None:
    assert client.put("/v1/settings/branding", json=body, headers=auth_a).status_code == 422


def test_logo_upload_download_and_delete(
    client: TestClient,
    auth_a: dict[str, str],
    auth_b: dict[str, str],
    tenant_a: Tenant,
    admin_db: psycopg.Connection[Any],
) -> None:
    image = png()
    uploaded = client.put(
        "/v1/settings/logo", content=image, headers={**auth_a, "Content-Type": "image/png"}
    )
    assert uploaded.status_code == 200 and uploaded.json()["branding"]["has_logo"] is True
    row = admin_db.execute(
        "select branding->>'logo_key' from organization_settings where org_id = %s",
        (tenant_a.org_id,),
    ).fetchone()
    assert row == (f"{tenant_a.org_id}/branding/logo",)

    fetched = client.get("/v1/settings/logo", headers=auth_a)
    assert fetched.status_code == 200 and fetched.content == image
    assert fetched.headers["content-type"] == "image/png"
    assert fetched.headers["x-content-type-options"] == "nosniff"
    assert (
        client.get("/v1/settings/logo", headers=auth_b).status_code == 404
    )  # B has none, and cannot see A's

    assert (
        client.delete("/v1/settings/logo", headers=auth_a).json()["branding"]["has_logo"] is False
    )
    assert client.get("/v1/settings/logo", headers=auth_a).status_code == 404


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", 415),
        (b"GIF89a" + b"\x00" * 20, 415),
        (b"<html><script>alert(1)</script></html>", 415),
        (b"", 415),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * (512 * 1024), 413),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, 422),  # no readable header
        (b"\xff\xd8\xff\xe0" + b"\x00" * 64, 422),
        (png_header_only(13000, 13000), 422),  # 20 KB on disk, more than 1 GB when decoded
        (png_header_only(2049, 16), 422),
        (png_header_only(0, 16), 422),
        (jpeg_header_only(40000, 40000), 422),
    ],
)
def test_bad_logos_are_refused(
    client: TestClient, auth_a: dict[str, str], content: bytes, expected: int
) -> None:
    # The declared type is ignored: the bytes decide.
    resp = client.put(
        "/v1/settings/logo", content=content, headers={**auth_a, "Content-Type": "image/png"}
    )
    assert resp.status_code == expected
    assert client.get("/v1/settings", headers=auth_a).json()["branding"]["has_logo"] is False


def test_members_can_read_but_not_change_settings(
    client: TestClient,
    make_token: TokenFactory,
    tenant_a: Tenant,
    admin_db: psycopg.Connection[Any],
) -> None:
    admin_db.execute(
        'update "member" set role = \'member\' where "userId" = %s', (tenant_a.user_id,)
    )
    member = bearer(make_token(tenant_a.user_id, tenant_a.org_id))
    assert client.get("/v1/settings", headers=member).json()["can_manage"] is False
    assert (
        client.put("/v1/settings/branding", json={"company_name": "x"}, headers=member).status_code
        == 403
    )
    assert client.put("/v1/settings/logo", content=png(), headers=member).status_code == 403
    assert client.delete("/v1/settings/logo", headers=member).status_code == 403
    assert client.get("/v1/settings").status_code == 401
