"""Manual language/category overrides for COMPLETED items.

Order of operations (chosen so the DB never points at a file that is not
there, and a failure leaves everything as it was):
  1. compute the new path and refuse collisions;
  2. rewrite TLAN/TALB (+ TXXX language frames) in place, keeping the old tag
     so it can be restored;
  3. os.replace the main file and each label sidecar into the new folder;
  4. update archive_state.db (method = MANUAL, new paths, new sha256/size);
  5. remove now-empty old folders.
Any failure in 2-4 rolls back the moves and the tags.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from mutagen.id3 import ID3, TALB, TLAN, TXXX

from . import classify, config
from .crawler import _sha256, target_path
from .state import State, now


class ReclassifyError(Exception):
    pass


LANGUAGES = sorted(k for k in classify.ISO639_2)


def _retag(path: Path, language: str, category: str) -> Path | None:
    """Rewrite language/category tags in place. Returns a .bak copy of the
    original file (restored on rollback), or None for untagged formats."""
    ext = path.suffix.lower()
    if ext not in (".mp3", ".flac"):
        return None
    backup = path.with_name(path.name + ".bak")
    shutil.copy2(path, backup)
    iso = classify.ISO639_2.get(language, "und")
    if ext == ".mp3":
        tags = ID3(path)
        tags.setall("TLAN", [TLAN(encoding=3, text=[iso])])
        tags.setall("TALB", [TALB(encoding=3, text=[category])])
        for desc, value in (("LANGUAGE", language), ("LANGUAGE_DETECTION_METHOD", "MANUAL")):
            tags.delall(f"TXXX:{desc}")
            tags.add(TXXX(encoding=3, desc=desc, text=[value]))
        tags.save(path, v2_version=4)
    else:
        from mutagen.flac import FLAC
        audio = FLAC(path)
        audio["LANGUAGE"], audio["ALBUM"] = [iso], [category]
        audio["LANGUAGE_NAME"], audio["LANGUAGE_DETECTION_METHOD"] = [language], ["MANUAL"]
        audio.save()
    return backup


def _prune_empty_dirs(start: Path, root: Path) -> None:
    root = root.resolve()
    d = start.resolve()
    while d != root and root in d.parents:
        try:
            d.rmdir()
        except OSError:
            break
        d = d.parent


def _relocate(state: State, row, new_row: dict, archive_root: Path, retag, db_updates: dict) -> dict:
    """Move an item's files to target_path(new_row), retag, update the DB.
    Steps 2-4 of the module docstring, with full rollback on any failure."""
    old_main = Path(row["local_filepath"])
    if not old_main.exists():
        raise ReclassifyError(f"file missing on disk: {old_main}")
    old_labels = [Path(p) for p in json.loads(row["label_local_paths"] or "[]")]
    new_main = target_path(new_row, Path(archive_root), old_main.suffix)
    moves: list[tuple[Path, Path]] = []
    if new_main != old_main:
        if new_main.exists():
            raise ReclassifyError(f"target already exists: {new_main}")
        moves.append((old_main, new_main))
        for lab in old_labels:
            if lab.name.startswith(old_main.stem):
                new_lab = new_main.with_name(new_main.stem + lab.name[len(old_main.stem):])
            else:
                new_lab = new_main.parent / lab.name
            moves.append((lab, new_lab))
    old_pdf = Path(row["pdf_local_path"]) if row["pdf_local_path"] else None
    new_pdf = new_main.with_suffix(".pdf") if old_pdf else None
    pdf_move = [(old_pdf, new_pdf)] if old_pdf and new_pdf != old_pdf else []

    backup = None
    done: list[tuple[Path, Path]] = []
    try:
        backup = retag(old_main, old_labels)
        for src, dst in moves + pdf_move:
            if not src.exists():
                continue  # a missing optional label scan is not fatal
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dst)
            done.append((src, dst))
        final_labels = [dst for src, dst in moves[1:] if (src, dst) in done] if moves else old_labels
        values = {**db_updates, "local_filepath": str(new_main),
                  "label_local_paths": json.dumps([str(p) for p in final_labels], ensure_ascii=False),
                  "pdf_local_path": str(new_pdf) if new_pdf and new_pdf.exists() else (
                      str(old_pdf) if old_pdf and old_pdf.exists() else None),
                  "file_sha256": _sha256(new_main), "file_size": new_main.stat().st_size,
                  "updated_at": now()}
        _update(state, row["item_id"], values)
    except Exception as exc:
        for src, dst in reversed(done):
            try:
                os.replace(dst, src)
            except OSError:
                pass
        if backup is not None:
            try:
                os.replace(backup, old_main)
            except OSError:
                pass
        raise ReclassifyError(f"rolled back: {exc}") from exc

    if backup is not None:
        backup.unlink(missing_ok=True)
    if moves or pdf_move:
        _prune_empty_dirs(old_main.parent, Path(archive_root))
    return dict(state.get_track(row["item_id"]))


def _update(state: State, item_id: str, values: dict) -> None:
    sets = ", ".join(k + "=?" for k in values)
    with state.conn:
        state.conn.execute("UPDATE tracks SET " + sets + " WHERE item_id=?",
                           (*values.values(), item_id))


def reclassify(state: State, item_id: str, language: str | None = None,
               category: str | None = None, archive_root: Path = config.ARCHIVE_ROOT) -> dict:
    row = state.get_track(item_id)
    if row is None:
        raise ReclassifyError(f"unknown item {item_id}")
    if row["status"] != "COMPLETED" or not row["local_filepath"]:
        raise ReclassifyError("only downloaded (COMPLETED) items can be re-classified")
    language = (language or row["language"]).strip()
    if language not in classify.ISO639_2:
        raise ReclassifyError(f"unsupported language '{language}'")
    category = (category if category is not None else row["category"] or "").strip()
    if not category:
        raise ReclassifyError("category must not be empty")
    changed_cat = f", category {row['category']!r}" if category != row["category"] else ""
    note = f"manual override (was {row['language']} via {row['language_detection_method']}{changed_cat})"
    new_row = {**dict(row), "language": language, "category": category}
    return _relocate(state, row, new_row, archive_root,
                     lambda path, _labels: _retag(path, language, category),
                     {"language": language, "category": category,
                      "language_detection_method": "MANUAL", "language_note": note})


# ---------------------------------------------------------------------------
# Re-filing after parser fixes (no network: uses _page_cache/)
# ---------------------------------------------------------------------------

def _full_retag(new_row: dict):
    """Retag with the complete metadata of new_row (same frames as a fresh download)."""
    from .crawler import Crawler
    from .media import sniff_image
    from .tagger import tag_audio

    def retag(path: Path, labels: list[Path]) -> Path | None:
        ext = path.suffix.lower()
        if ext not in (".mp3", ".flac"):
            return None
        backup = path.with_name(path.name + ".bak")
        shutil.copy2(path, backup)
        first = next((p for p in labels if p.exists()), None)
        label = (first.read_bytes(), sniff_image(first)[1]) if first else None
        tag_audio(path, ext, Crawler._tag_meta(new_row), label)
        return backup
    return retag


def _missing_language_tag(path: Path) -> bool:
    """True for an MP3/FLAC without TLAN/LANGUAGE (e.g. a FLAC downloaded
    before FLAC tagging existed)."""
    from .tagger import verify_audio
    ext = path.suffix.lower()
    if ext not in (".mp3", ".flac") or not path.exists():
        return False
    try:
        return not verify_audio(path, ext).get("TLAN")
    except Exception:
        return False


def refile_from_cache(state: State, archive_root: Path = config.ARCHIVE_ROOT,
                      cache_dir: Path = config.PAGE_CACHE_DIR) -> dict:
    """Re-parse cached details pages with the current parser and re-file
    COMPLETED items whose metadata changed. MANUAL overrides are never touched.

    Same media URLs   -> move + retag in place (no download).
    Media URLs differ -> old files go to _superseded/<item_id>/ (nothing is
                         deleted) and the item returns to PENDING."""
    import gzip

    from .crawler import build_record, cache_path
    from .net import decode_html
    from .parser import parse_details
    from .state import TRACK_FIELDS

    root = Path(archive_root)
    stats = {"unchanged": 0, "refiled": 0, "requeued": 0, "no_cache": 0, "errors": []}
    rows = state.conn.execute(
        "SELECT * FROM tracks WHERE status='COMPLETED' AND "
        "COALESCE(language_detection_method,'') != 'MANUAL'").fetchall()
    for row in rows:
        cached = cache_path(cache_dir, row["source_url"])
        if not cached.exists():
            stats["no_cache"] += 1
            continue
        item = parse_details(decode_html(gzip.decompress(cached.read_bytes())), row["source_url"])
        record = build_record(item)
        fresh = {k: record[k] for k in TRACK_FIELDS}
        new_row = {**dict(row), **fresh}
        meta_changed = any((row[k] or "") != (record[k] or "") for k in TRACK_FIELDS
                           if k != "raw_metadata_json")
        same_media = (row["audio_url"] == record["audio_url"]
                      and json.loads(row["label_image_urls"] or "[]")
                      == json.loads(record["label_image_urls"] or "[]"))
        try:
            if same_media:
                current = Path(row["local_filepath"])
                if (not meta_changed and current == target_path(new_row, root, current.suffix)
                        and not _missing_language_tag(current)):
                    stats["unchanged"] += 1
                    continue
                _relocate(state, row, new_row, root, _full_retag(new_row), fresh)
                stats["refiled"] += 1
                if fresh.get("pdf_url") and not row["pdf_local_path"]:
                    # Gained a PDF: keep the files, let the crawler fetch only the PDF.
                    _update(state, row["item_id"], {
                        "status": "PENDING", "updated_at": now(),
                        "last_error": "PDF pending (re-queued by --refile-cached)"})
                    stats["pdf_pending"] = stats.get("pdf_pending", 0) + 1
            else:
                quarantine = root.parent / "_superseded" / row["item_id"]
                quarantine.mkdir(parents=True, exist_ok=True)
                old = [Path(row["local_filepath"])] + [
                    Path(p) for p in json.loads(row["label_local_paths"] or "[]")]
                for f in old:
                    if f.exists():
                        os.replace(f, quarantine / f.name)
                        _prune_empty_dirs(f.parent, root)
                _update(state, row["item_id"], {
                    **fresh, "status": "PENDING", "local_filepath": None,
                    "label_local_paths": None, "file_sha256": None, "file_size": None,
                    "updated_at": now(),
                    "last_error": "re-queued by --refile-cached (media URLs changed)"})
                stats["requeued"] += 1
        except ReclassifyError as exc:
            stats["errors"].append(f"{row['item_id']}: {exc}")
    return stats
