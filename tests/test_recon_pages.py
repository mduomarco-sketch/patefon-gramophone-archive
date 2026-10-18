"""Regression checks against the live pages saved by --recon in _recon/.

Skipped when those files are absent (fresh checkout). Assertions are
structural - ids, URLs and which fields were found - not page text."""

from pathlib import Path

import pytest

from conftest import BASE
from rr_archive import classify
from rr_archive.crawler import build_record
from rr_archive.net import decode_html
from rr_archive.parser import parse_details, parse_listing

RECON = Path(__file__).resolve().parent.parent / "_recon"


def load(name):
    p = RECON / name
    if not p.exists():
        pytest.skip(f"_recon/{name} not present")
    return decode_html(p.read_bytes())


def test_listing_audio_hint(fake_site):
    cat = parse_listing(fake_site[BASE + "categories.php?cat_id=1"][2].decode("cp1251"),
                        BASE + "categories.php?cat_id=1")
    assert cat.audio_items == {"101"}
    news = parse_listing(fake_site[BASE + "categories.php?cat_id=5"][2].decode("cp1251"),
                         BASE + "categories.php?cat_id=5")
    assert news.audio_items == set()


def test_real_index():
    idx = parse_listing(load("index.html"), BASE + "index.php")
    assert len(idx.subcategories) > 20
    assert all(cid.isdigit() and name for cid, name, _ in idx.subcategories)


def test_real_label_category():
    lst = parse_listing(load("label_cat9.html"), BASE + "categories.php?cat_id=9")
    assert lst.name and lst.items and lst.subcategories
    assert all(name for _, name, _ in lst.items)          # shownote() titles recovered
    assert lst.audio_items and lst.audio_items <= {i for i, _, _ in lst.items}


def test_real_record_page():
    item = parse_details(load("record_22176.html"), BASE + "details.php?image_id=22176")
    assert item.image_id == "22176" and item.title
    assert len(item.breadcrumbs) >= 2 and all(c.cat_id and c.name and "." != c.name.strip()[:1]
                                              for c in item.breadcrumbs)
    for key in ("title", "language", "catalog_category", "genre", "artist", "composer",
                "year", "catalog", "matrix", "label", "original_title"):
        assert item.fields.get(key), key
    assert item.audio_urls == [BASE + "data/media/696/876.mp3"]
    assert item.image_urls == [BASE + "data/media/696/876.jpg"]   # no userpic/QR/thumbs
    assert item.description
    rec = build_record(item)
    assert rec["media_type"] == classify.MUSIC and rec["year"] == "1906"
    assert rec["language"] == "Instrumental"


def test_real_book_page():
    item = parse_details(load("details.html"), BASE + "details.php?image_id=132")
    assert item.extra.get("has_pdf") and not item.audio_urls
    assert item.image_urls and all("/data/media/" in u for u in item.image_urls)
    rec = build_record(item)
    assert rec["media_type"] == classify.PRINT and rec["language"] == "Russian"
