"""Checks on the tenant's logo that do not need an image library.

A PNG or JPEG of a few kilobytes can declare tens of thousands of pixels per side. The
PDF renderer decodes the logo in the shared worker, where such a file costs more than a
gigabyte of memory, so the declared size is read from the header and limited, both when
the logo is uploaded and again before it is put into a report.
"""

import struct

MAX_LOGO_SIDE = 2048  # pixels; the report shows the logo at 220 x 64 at most

_PNG = b"\x89PNG\r\n\x1a\n"
# Start-of-frame markers carry the size. 0xC4, 0xC8 and 0xCC are not frames.
_JPEG_FRAMES = set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
_JPEG_NO_LENGTH = {0x01, *range(0xD0, 0xD8)}


def detect_type(data: bytes) -> str | None:
    """The content type by magic bytes. SVG and everything else is None."""
    if data.startswith(_PNG):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def dimensions(data: bytes) -> tuple[int, int] | None:
    """(width, height) as declared in the header, or None when it cannot be read."""
    kind = detect_type(data)
    if kind == "image/png":
        # Signature, then the IHDR chunk: length, "IHDR", width, height.
        if len(data) < 24 or data[12:16] != b"IHDR":
            return None
        width, height = struct.unpack(">II", data[16:24])
        return width, height
    if kind == "image/jpeg":
        position = 2
        while position + 4 <= len(data):
            if data[position] != 0xFF:
                return None
            marker = data[position + 1]
            if marker == 0xFF:  # fill byte
                position += 1
                continue
            if marker in _JPEG_NO_LENGTH:
                position += 2
                continue
            (length,) = struct.unpack(">H", data[position + 2 : position + 4])
            if length < 2:
                return None
            if marker in _JPEG_FRAMES:
                if position + 9 > len(data):
                    return None
                height, width = struct.unpack(">HH", data[position + 5 : position + 9])
                return width, height
            if marker == 0xDA:  # image data starts; no frame header was seen
                return None
            position += 2 + length
    return None


def acceptable(data: bytes) -> bool:
    """A PNG or JPEG whose declared size is within ``MAX_LOGO_SIDE`` on both sides."""
    size = dimensions(data)
    return size is not None and all(0 < side <= MAX_LOGO_SIDE for side in size)
