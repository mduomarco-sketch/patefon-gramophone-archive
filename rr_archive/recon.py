"""Reconnaissance: parse a handful of pages and report what each selector
matched. Writes _recon/report.json (rendered by the dashboard) and prints a
console summary."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .crawler import build_record, target_path
from .net import PoliteClient, decode_html
from .parser import (canonical_url, looks_like_details, page_kind, parse_details, parse_listing,
                     source_url_hint)


def listing_report(listing, source: str) -> dict:
    return {
        "kind": "listing", "source": source, "url": listing.url, "cat_id": listing.cat_id,
        "name": listing.name,
        "breadcrumbs": [[c.cat_id, c.name] for c in listing.breadcrumbs],
        "subcategories": [list(x) for x in listing.subcategories],
        "items": [list(x) for x in listing.items],
        "pagination": listing.pages, "notes": listing.notes,
        "checks": {
            "breadcrumbs_found": bool(listing.breadcrumbs) or listing.cat_id is None,
            "has_links": bool(listing.subcategories or listing.items),
        },
    }


def item_report(item, source: str, root: Path) -> dict:
    rec = build_record(item)
    return {
        "kind": "details", "source": source, "url": item.url, "image_id": item.image_id,
        "title": item.title,
        "breadcrumbs": [[c.cat_id, c.name] for c in item.breadcrumbs],
        "raw_fields": item.raw_fields, "fields": item.fields,
        "audio_urls": item.audio_urls, "image_urls": item.image_urls,
        "download_url": item.download_url, "description": item.description[:2000],
        "record": {k: rec[k] for k in ("media_type", "language", "language_detection_method",
                                       "language_note", "category", "category_path", "artist",
                                       "composer", "year", "record_label", "catalog_number",
                                       "matrix_number")},
        "path_preview": str(target_path(rec, root, ".mp3")),
        "notes": item.notes,
        "checks": {
            "image_id": bool(item.image_id),
            "title": bool(item.title),
            "breadcrumbs_found": bool(item.breadcrumbs),
            "info_table_rows": len(item.raw_fields),
            "mapped_fields": len(item.fields),
            "audio_found": bool(item.audio_urls),
            "label_images_found": bool(item.image_urls),
            "artist_found": bool(rec["artist"]),
            "year_found": bool(rec["year"]),
        },
    }


def _print(entry: dict) -> None:
    print(f"\n### {entry['source']}  ->  {entry['url']}")
    for k, v in entry.items():
        if k in ("source", "url"):
            continue
        text = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
        print(f"  {k}: {text[:500]}")


def _write(entries: list[dict], mode: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.json"
    path.write_text(json.dumps({
        "mode": mode, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages": entries}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def recon_local(directory: Path, root: Path, out_dir: Path = config.RECON_DIR) -> list[dict]:
    entries = []
    for p in sorted(x for x in Path(directory).rglob("*") if x.suffix.lower() in (".html", ".htm")):
        html = decode_html(p.read_bytes())
        hint = source_url_hint(html)
        url = canonical_url(hint) if hint else None
        if (url and page_kind(url) == "details") or (not url and looks_like_details(html)):
            entry = item_report(parse_details(html, url), p.name, root)
        else:
            entry = listing_report(parse_listing(html, url or config.START_URL), p.name)
        entries.append(entry)
        _print(entry)
    _write(entries, "local", out_dir)
    return entries


def recon_online(client: PoliteClient, root: Path, out_dir: Path = config.RECON_DIR) -> list[dict]:
    """index -> first category (descending until items appear) -> first item.
    3-5 polite requests; raw pages saved next to report.json. CaptchaRequired
    propagates to the caller untouched."""
    out_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []

    def get(url, name):
        _final, body, html = client.fetch_html(url)
        (out_dir / name).write_bytes(body)
        return html

    try:
        listing = parse_listing(get(config.START_URL, "index.html"), config.START_URL)
        entries.append(listing_report(listing, "index.html"))
        _print(entries[-1])
        if not listing.subcategories:
            return entries
        cat_url, depth = listing.subcategories[0][2], 0
        while True:
            name = "category.html" if depth == 0 else f"category_{depth + 1}.html"
            listing = parse_listing(get(cat_url, name), cat_url)
            entries.append(listing_report(listing, name))
            _print(entries[-1])
            if listing.items or not listing.subcategories or depth >= 2:
                break
            cat_url, depth = listing.subcategories[0][2], depth + 1
        if listing.items:
            item_url = listing.items[0][2]
            entries.append(item_report(parse_details(get(item_url, "details.html"), item_url),
                                       "details.html", root))
            _print(entries[-1])
        return entries
    finally:
        _write(entries, "online", out_dir)
