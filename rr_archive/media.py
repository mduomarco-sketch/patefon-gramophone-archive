"""Magic-byte validation so HTML error pages never land in the archive."""

from __future__ import annotations

from pathlib import Path


class InvalidMedia(Exception):
    pass


def _skip_id3(head: bytes) -> int:
    if head[:3] == b"ID3" and len(head) >= 10:
        size = 0
        for b in head[6:10]:
            size = (size << 7) | (b & 0x7F)
        return 10 + size
    return 0


def sniff_audio(path: Path) -> str:
    """Return the audio container extension ('.mp3', '.flac', ...) or raise."""
    with open(path, "rb") as fh:
        head = fh.read(16)
        if head[:4] == b"fLaC":
            return ".flac"
        if head[:4] == b"OggS":
            return ".ogg"
        if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
            return ".wav"
        if head[4:8] == b"ftyp":
            return ".m4a"
        if head[:16] == bytes.fromhex("3026B2758E66CF11A6D900AA0062CE6C"):
            return ".wma"
        offset = _skip_id3(head)
        fh.seek(offset)
        frame = fh.read(4096)
    # MPEG audio frame sync: 11 set bits, a valid layer and bitrate index.
    for i in range(len(frame) - 1):
        if frame[i] == 0xFF and (frame[i + 1] & 0xE0) == 0xE0:
            layer = (frame[i + 1] >> 1) & 0x03
            bitrate = (frame[i + 2] >> 4) if i + 2 < len(frame) else 0
            if layer != 0 and bitrate not in (0, 15):
                return ".mp3"
        if i > 2048:
            break
    if offset:
        return ".mp3"  # ID3-tagged with padding before the first frame
    raise InvalidMedia(f"not a recognised audio file (starts with {head[:12]!r})")


def sniff_pdf(path: Path) -> None:
    """Raise unless path is a complete PDF. view_pdf.php sends no
    Content-Length, so completeness is checked via the trailing %%EOF."""
    size = path.stat().st_size
    with open(path, "rb") as fh:
        head = fh.read(8)
        fh.seek(max(0, size - 2048))
        tail = fh.read()
    if not head.startswith(b"%PDF-"):
        raise InvalidMedia(f"not a PDF (starts with {head!r})")
    if b"%%EOF" not in tail:
        raise InvalidMedia("truncated PDF (no %%EOF marker)")


def sniff_image(path: Path) -> tuple[str, str]:
    """Return (extension, mime) or raise."""
    with open(path, "rb") as fh:
        head = fh.read(16)
    if head[:3] == b"\xff\xd8\xff":
        return ".jpg", "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png", "image/png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif", "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp", "image/webp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return ".tif", "image/tiff"
    if head[:2] == b"BM":
        return ".bmp", "image/bmp"
    raise InvalidMedia(f"not a recognised image (starts with {head[:12]!r})")
