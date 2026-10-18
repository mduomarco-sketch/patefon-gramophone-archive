"""ID3v2.4 tagging with mutagen."""

from __future__ import annotations

import re
from pathlib import Path

from mutagen.id3 import (APIC, COMM, ID3, TALB, TCOM, TCON, TDRC, TEXT, TIT2, TLAN, TPE1,
                         TPUB, TXXX, WOAS, ID3NoHeaderError)
from mutagen.mp3 import MP3


def first_year(text: str | None) -> str | None:
    m = re.search(r"\b(1[89]\d\d|20[0-2]\d)\b", text or "")
    return m.group(1) if m else None


def tag_mp3(path: Path, meta: dict, label_image: tuple[bytes, str] | None = None) -> None:
    """Write tags to path (works on the .part file before the atomic rename).

    meta keys: title, artist, composer, lyricist, album, genre, year,
    language_iso, language, language_method, publisher, catalog_number,
    matrix_number, source_url, notes, item_id, category_path."""
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    def text_frame(cls, value):
        if value:
            tags.setall(cls.__name__, [cls(encoding=3, text=[str(value)])])

    text_frame(TIT2, meta.get("title"))
    text_frame(TPE1, meta.get("artist"))
    text_frame(TCOM, meta.get("composer"))
    text_frame(TEXT, meta.get("lyricist"))
    text_frame(TALB, meta.get("album"))
    text_frame(TCON, meta.get("genre"))
    text_frame(TPUB, meta.get("publisher"))
    year = first_year(meta.get("year"))
    if year:
        tags.setall("TDRC", [TDRC(encoding=3, text=[year])])
    lang = meta.get("language_iso") or "und"
    tags.setall("TLAN", [TLAN(encoding=3, text=[lang])])

    comment = "\n".join(x for x in (f"Source: {meta.get('source_url', '')}", meta.get("notes") or "") if x)
    tags.setall("COMM", [COMM(encoding=3, lang=lang if len(lang) == 3 else "und",
                              desc="", text=[comment])])
    if meta.get("source_url"):
        tags.setall("WOAS", [WOAS(url=meta["source_url"])])

    tags.delall("TXXX")
    for desc, key in (("ITEM_ID", "item_id"), ("LANGUAGE", "language"),
                      ("LANGUAGE_DETECTION_METHOD", "language_method"),
                      ("CATALOG_NUMBER", "catalog_number"), ("MATRIX_NUMBER", "matrix_number"),
                      ("CATEGORY_PATH", "category_path"), ("YEAR_ORIGINAL", "year")):
        if meta.get(key):
            tags.add(TXXX(encoding=3, desc=desc, text=[str(meta[key])]))

    if label_image:
        data, mime = label_image
        tags.setall("APIC", [APIC(encoding=3, mime=mime, type=3, desc="Record label", data=data)])

    tags.save(path, v2_version=4)


def tag_flac(path: Path, meta: dict, label_image: tuple[bytes, str] | None = None) -> None:
    """Vorbis-comment equivalent of tag_mp3 (FLAC has no ID3). LANGUAGE holds
    the same ISO 639-2 code that TLAN does for MP3."""
    from mutagen.flac import FLAC, Picture

    audio = FLAC(path)
    lang = meta.get("language_iso") or "und"
    comments = {
        "TITLE": meta.get("title"), "ARTIST": meta.get("artist"),
        "COMPOSER": meta.get("composer"), "LYRICIST": meta.get("lyricist"),
        "ALBUM": meta.get("album"), "GENRE": meta.get("genre"),
        "DATE": first_year(meta.get("year")), "LANGUAGE": lang,
        "ORGANIZATION": meta.get("publisher"), "CATALOGNUMBER": meta.get("catalog_number"),
        "MATRIX_NUMBER": meta.get("matrix_number"), "ITEM_ID": meta.get("item_id"),
        "LANGUAGE_NAME": meta.get("language"),
        "LANGUAGE_DETECTION_METHOD": meta.get("language_method"),
        "CATEGORY_PATH": meta.get("category_path"), "YEAR_ORIGINAL": meta.get("year"),
        "SOURCE_URL": meta.get("source_url"),
        "COMMENT": "\n".join(x for x in (f"Source: {meta.get('source_url', '')}",
                                         meta.get("notes") or "") if x),
    }
    for key, value in comments.items():
        if value:
            audio[key] = [str(value)]
        elif key in audio:
            del audio[key]
    if label_image:
        data, mime = label_image
        pic = Picture()
        pic.type, pic.mime, pic.desc, pic.data = 3, mime, "Record label", data
        audio.clear_pictures()
        audio.add_picture(pic)
    audio.save()


def verify_flac(path: Path) -> dict:
    from mutagen.flac import FLAC

    audio = FLAC(path)
    if not audio.info or audio.info.length <= 0:
        raise ValueError("FLAC has no audio length")
    return {
        "length": round(audio.info.length, 2),
        "bitrate": getattr(audio.info, "bitrate", 0),
        "TLAN": (audio.get("LANGUAGE") or [""])[0],
        "TIT2": (audio.get("TITLE") or [""])[0],
        "APIC": bool(audio.pictures),
    }


def tag_audio(path: Path, ext: str, meta: dict, label_image=None) -> bool:
    """Tag by container. Returns False when the format is left untagged."""
    if ext == ".mp3":
        tag_mp3(path, meta, label_image)
        return True
    if ext == ".flac":
        tag_flac(path, meta, label_image)
        return True
    return False


def verify_audio(path: Path, ext: str) -> dict:
    if ext == ".mp3":
        return verify_mp3(path)
    if ext == ".flac":
        return verify_flac(path)
    return {}


def verify_mp3(path: Path) -> dict:
    """Re-open the tagged file; raises if mutagen can't parse audio frames."""
    audio = MP3(path)
    if not audio.info or audio.info.length <= 0:
        raise ValueError("MP3 has no decodable frames")
    tags = audio.tags or {}
    return {
        "length": round(audio.info.length, 2),
        "bitrate": audio.info.bitrate,
        "TLAN": str(tags.get("TLAN", "")),
        "TIT2": str(tags.get("TIT2", "")),
        "APIC": bool(tags.getall("APIC")) if hasattr(tags, "getall") else False,
    }
