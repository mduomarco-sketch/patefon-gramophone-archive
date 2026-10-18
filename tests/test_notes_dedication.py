"""The archive's own texts (label / liner / work), the dedication, and the
gift-polish UI pieces. All fixture text is invented."""

import json
from pathlib import Path

import pytest

import rr_archive.notes as notes_mod
from conftest import BASE, details_page, row, sub
from rr_archive.export import STATIC_DIR, export_catalog
from rr_archive.parser import parse_details
from rr_archive.state import State

STATIC = Path(STATIC_DIR)


def page_with_liner():
    return details_page(
        201, [(1, "Тестовые записи")], "Test song (Тестовая песня), song", ["201", "Тестовая песня.", "Исп. Тестов."],
        [row("Title Name", "Test song"),
         row("Language(s) or Ethnics", "russian" + sub("Catalog category", "Vocal")),
         row("Artist(s)", "Тестовый Певец", "row2"),
         row("Larger Composition Name", "Тестовая опера, акт 2"),
         row("Additional information", "Первая строка заметки<br />вторая строка<br />третья строка", "row2")],
        media_html='<audio src="./data/media/1/x.mp3"></audio>').decode("cp1251")


def test_liner_notes_keep_line_breaks_and_work_is_mapped():
    item = parse_details(page_with_liner(), BASE + "details.php?image_id=201")
    assert item.fields["notes"].split("\n") == ["Первая строка заметки", "вторая строка", "третья строка"]
    assert item.fields["work"] == "Тестовая опера, акт 2"


def test_extract_returns_label_liner_work():
    e = notes_mod.extract(page_with_liner(), BASE + "details.php?image_id=201")
    assert e["label"].splitlines() == ["201", "Тестовая песня.", "Исп. Тестов."]
    assert e["liner"].count("\n") == 2 and e["work"].startswith("Тестовая опера")


def test_notes_index_and_export_embed(tmp_path, make_client, monkeypatch):
    from rr_archive.crawler import Crawler
    idx = tmp_path / "notes_index.json"
    monkeypatch.setattr(notes_mod, "INDEX_PATH", idx)
    monkeypatch.setattr(notes_mod.build_index, "__defaults__", (tmp_path / "cache", idx))
    monkeypatch.setattr(notes_mod.load_index, "__defaults__", (idx,))
    state = State(tmp_path / "s.db")
    crawler = Crawler(make_client(), state, tmp_path / "archive", cache_dir=tmp_path / "cache",
                      control_dir=tmp_path / "ctl")
    crawler.seed()
    crawler.run()
    stats = notes_mod.build_index(state.conn)
    assert stats["with_text"] >= 5 and stats["no_cached_page"] == 0
    out = export_catalog(state, tmp_path / "archive")
    state.close()
    data = json.loads(out.read_text(encoding="utf-8").split('<script id="rr-data" type="application/json">', 1)[1]
                      .split("</script>", 1)[0])
    by_id = {i["id"]: i for i in data["items"]}
    assert by_id["101"]["notes"]["label"].splitlines()[-1] == "Романсъ."
    # A dedication is embedded; its text comes from dedication.json or the
    # committed example fallback. Don't assert a personal value (the real
    # dedication.json is git-ignored and absent on a fresh clone).
    assert isinstance(data["dedication"]["text"], str) and data["dedication"]["text"].strip()


def test_dedication_example_is_valid_and_neutral():
    # The committed public fallback (dedication.json itself is git-ignored).
    d = json.loads((STATIC / "dedication.example.json").read_text(encoding="utf-8"))
    assert d["text"] == "A gift of old records"


def test_ui_has_dedication_tabs_crackle_in_all_languages():
    js = (STATIC / "catalog.js").read_text(encoding="utf-8")
    for s in ("Open the Gramophone 🎵", "Открыть патефон 🎵", "Патефонды ашу 🎵",
              "📜 Lyrics & Notes", "📜 Слова и заметки", "📜 Сөздер мен жазбалар",
              "The historical parchment for this recording is silent",
              "dedicationSeen", "openDedication", "A gift of old records", "disc-dust"):
        assert s in js, s
    assert "crackle" not in js.lower().replace("crackling", "")      # needle crackle removed
    css = (STATIC / "catalog.css").read_text(encoding="utf-8")
    for s in (".pf-ded-wrap", ".pf-ded-seal", ".pf-tabs", ".pf-verse", ".pf-dropcap", ".disc .disc-dust", "--parchment"):
        assert s in css, s
    assert css.count("{") == css.count("}")


def test_notes_index_is_incremental(tmp_path, make_client):
    """Exports used to re-parse every cached page on every click (~12 min for
    17k records). Only new or re-fetched pages may be parsed again."""
    import os
    from rr_archive.crawler import Crawler, cache_path
    state = State(tmp_path / "s.db")
    crawler = Crawler(make_client(), state, tmp_path / "archive", cache_dir=tmp_path / "cache",
                      control_dir=tmp_path / "ctl")
    crawler.seed()
    crawler.run()
    cache, idx = tmp_path / "cache", tmp_path / "notes_index.json"
    first = notes_mod.build_index(state.conn, cache, idx)
    assert first["parsed"] == first["records"] - first["no_cached_page"] and first["with_text"] >= 5
    again = notes_mod.build_index(state.conn, cache, idx)
    assert again["parsed"] == 0 and again["with_text"] == first["with_text"]
    assert json.loads(idx.read_text(encoding="utf-8")) == notes_mod.load_index(idx)
    url = state.conn.execute("SELECT source_url FROM tracks WHERE status='COMPLETED' LIMIT 1").fetchone()[0]
    page = cache_path(cache, url)
    st = page.stat()
    os.utime(page, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))   # page re-fetched
    third = notes_mod.build_index(state.conn, cache, idx)
    assert third["parsed"] == 1 and third["with_text"] == first["with_text"]
    assert notes_mod.build_index(state.conn, cache, idx, full=True)["parsed"] == first["parsed"]
    state.close()
