import json
import sqlite3

import pytest
from mutagen.id3 import ID3

from conftest import BASE, page
from rr_archive import classify, config
from rr_archive.crawler import Crawler
from rr_archive.media import InvalidMedia, sniff_audio
from rr_archive.net import CaptchaRequired, PoliteClient, SessionProblem, load_cookies
from rr_archive.parser import canonical_url, parse_details, parse_listing
from rr_archive.paths import build_path, sanitize_segment
from rr_archive.state import State


# --- parser ---------------------------------------------------------------------

def test_canonical_url_strips_session_and_sort():
    assert canonical_url("./categories.php?cat_id=3&sessionid=x&sortorder=ASC&page=1") == \
        BASE + "categories.php?cat_id=3"
    assert canonical_url("details.php?image_id=7&mode=search#c") == BASE + "details.php?image_id=7"
    assert canonical_url("https://russian-records.com/") == BASE + "index.php"


def _html(site, key):
    return site[BASE + key][2].decode("cp1251")


def test_parse_index_and_category(fake_site):
    idx = parse_listing(_html(fake_site, "index.php"), BASE + "index.php")
    assert [c[0] for c in idx.subcategories] == ["10", "2", "3", "4", "5"]
    cat = parse_listing(_html(fake_site, "categories.php?cat_id=1"), BASE + "categories.php?cat_id=1")
    # top-level crumb arrives as a link, the current category as plain '>' text
    assert cat.name == "Русские романсы"
    assert [(c.cat_id, c.name) for c in cat.breadcrumbs] == [("10", "Русские пластинки")]
    # thumbnail-only link: title comes from shownote(), uploader suffix stripped
    assert cat.items == [("101", "Evening bells (Вечерний звон), romance",
                          BASE + "details.php?image_id=101")]
    assert cat.pages == [BASE + "categories.php?cat_id=1&page=2"]


def test_parse_details_real_dom(fake_site):
    item = parse_details(_html(fake_site, "details.php?image_id=101"), BASE + "details.php?image_id=101")
    assert item.image_id == "101"
    assert item.title == "Evening bells (Вечерний звон), romance"         # font.section-title
    # '. .' crumb resolved from title=, '&l=english' dropped by canonicalisation
    assert [(c.cat_id, c.name) for c in item.breadcrumbs] == [("10", "Русские пластинки"),
                                                               ("1", "Русские романсы")]
    f = item.fields
    assert f["title"] == "Evening bells"
    assert f["artist"] == "Тестовый Певец" and f["composer"] == "Тестовый Композитор"
    assert f["lyricist"] == "Тестовый Поэт" and f["accompaniment"] == "piano"
    # packed sub-fields: 'russian | Catalog category: Vocal | Genre (Music Category): Romance'
    assert f["language"] == "russian" and f["catalog_category"] == "Vocal" and f["genre"] == "Romance"
    assert f["recording_place"] == "Москва" and f["year"] == "ок. 1912"
    assert f["record_size"] == "25 cm" and f["label_size"] == "90 mm"
    # Label | Catalog No | Mx/Ctr No table (this side, not the reverse row)
    assert (f["label"], f["catalog"], f["matrix"]) == ("Граммофон", "22-12345", "1234ab")
    assert item.extra["catalog_table"]["reverse_side"]["catalog no"] == "X-1"
    assert f["mirror_transcription"] == "TEST-101"
    # unlabelled transcription row -> description + original-script text for detection
    assert item.description.splitlines() == ["101", "Evening bells", "Вечерній звонъ.", "Романсъ."]
    assert "Вечерній звонъ." in f["original_title"]
    # own <audio> only - not the nav strip's data-src of item 106
    assert item.audio_urls == [BASE + "data/media/1/zvon.mp3"]
    # full-size scan only: no userpic, QR code, own thumbnail or other items' thumbnails
    assert item.image_urls == [BASE + "data/media/1/zvon.jpg"]
    assert BASE + "details.php?image_id=106" in item.linked_items
    # counters/comment widgets are not metadata
    from rr_archive.parser import notes_fields
    labels = [k for k, _ in notes_fields(item.raw_fields)]
    assert "Hits" not in labels and "Audio downloads" not in labels and "Added by" in labels
    assert not any(k.startswith("[") for k in item.raw_fields)


def test_pdf_item_is_print(fake_site):
    from rr_archive.crawler import build_record
    item = parse_details(_html(fake_site, "details.php?image_id=105"), BASE + "details.php?image_id=105")
    assert item.extra.get("has_pdf") and not item.audio_urls
    rec = build_record(item)
    assert rec["media_type"] == classify.PRINT and rec["year"] == "1913"


def test_details_id_from_saved_page():
    html = ("<!-- saved from url=(0055)https://www.russian-records.com/details.php?image_id=42 -->"
            + page('<b class="title">X</b>').decode("cp1251"))
    assert parse_details(html).image_id == "42"


# --- classifier -------------------------------------------------------------------

def ctx(title, cats=(), artist="", desc="", lang="", lyricist=""):
    return classify.ItemContext(title=title, original_title="", category_path=list(cats),
                                description=desc, keywords="", language_field=lang, artist=artist,
                                accompaniment="", genre="", lyricist=lyricist, has_audio=True,
                                has_images=False)


@pytest.mark.parametrize("c,expected,method", [
    (ctx("Вечерний звон"), "Russian", None),                      # lingua says Ukrainian 0.50; must stay Russian
    (ctx("Калинка моя"), "Russian", None),
    (ctx("Сіяніе луны"), "Russian", classify.SCRIPT_NLP),        # pre-reform, not Belarusian
    (ctx("Хорошъ вечеръ"), "Russian", classify.SCRIPT_NLP),
    (ctx("Ой, на горі два дубки"), "Ukrainian", classify.SCRIPT_NLP),
    (ctx("Їхав козак за Дунай"), "Ukrainian", classify.SCRIPT_NLP),
    (ctx("Песня", cats=["Украинские песни"]), "Ukrainian", classify.EXPLICIT),
    (ctx("Песня (укр.)"), "Ukrainian", classify.EXPLICIT),
    (ctx("Песня", desc="Исполняется на польском языке"), "Polish", classify.EXPLICIT),
    (ctx("Очи чёрные", cats=["Цыганские романсы"]), "Russian", classify.SCRIPT_NLP),  # gypsy romance is Russian
    (ctx("ביי מיר ביסטו שיין"), "Yiddish", classify.SCRIPT_NLP),
    (ctx("სულიკო"), "Georgian", classify.SCRIPT_NLP),
    (ctx("Марш", cats=["Марши"], artist="Духовой оркестр"), "Instrumental", classify.INSTRUMENTAL),
    (ctx("Вальс", artist="Струнный оркестр"), "Instrumental", classify.INSTRUMENTAL),
    (ctx("Марш", cats=["Марши"], artist="Хор и оркестр"), "Russian", None),  # choir -> vocal
    (ctx("Танго", artist="Певица", desc="поёт в сопр. оркестра"), "Russian", None),
    (ctx("1234"), "Unknown_Language", classify.UNKNOWN),
])
def test_language(c, expected, method):
    r = classify.classify_language(c, classify.MUSIC)
    assert r.language == expected, r
    if method:
        assert r.method == method, r


def test_media_types():
    assert classify.classify_media(ctx("Выступление", cats=["Радиозаписи"])) == classify.RADIO
    assert classify.classify_media(ctx("Песня о радио")) == classify.MUSIC
    c = ctx("Газета", cats=["Газеты"]); c.has_audio = False; c.has_images = True
    assert classify.classify_media(c) == classify.PRINT


# --- paths -------------------------------------------------------------------------

def test_sanitize():
    assert sanitize_segment('A/B:C*?"<>|. ', "x") == "A-B -C'-"
    assert sanitize_segment("  ", "Unknown_Artist") == "Unknown_Artist"
    assert sanitize_segment("CON", "x") == "CON_"
    assert len(sanitize_segment("я" * 300, "x")) == 120


def test_path_budget(tmp_path):
    p = build_path(tmp_path, ["Russian", "К" * 120, "А" * 120], "Т" * 120, "1912", "101", ".mp3")
    assert len(str(p.resolve())) <= config.PATH_BUDGET
    assert p.name.endswith(" [101].mp3") and p.name.startswith("1912 - ")


# --- network safety ----------------------------------------------------------------

def test_captcha_redirect_halts(make_client):
    site = {BASE + "index.php": (302, {"Location": "https://www.russian-records.com/get_captcha.php"}, b"")}
    client = make_client(site)
    with pytest.raises(CaptchaRequired):
        client.fetch_html(BASE + "index.php")
    assert not any("captcha" in u for u in client.request_log)  # never requested


def test_captcha_html_halts(make_client):
    body = b'<form method="post" action="get_captcha.php"><input name="proverka"></form>'
    client = make_client({BASE + "x.mp3": (200, {"Content-Type": "text/html"}, body)})
    with pytest.raises(CaptchaRequired):
        client.download(BASE + "x.mp3", __import__("pathlib").Path("unused.part"))


def test_backoff_and_403(make_client, monkeypatch):
    monkeypatch.setattr(config, "MAX_RETRIES", 2)
    sleeps = []
    client = make_client({BASE + "index.php": (503, {}, b"")}, sleeps)
    with pytest.raises(Exception):
        client.fetch_html(BASE + "index.php")
    assert [int(s) for s in sleeps if s >= 5][:2] == [5, 10]
    client = make_client({BASE + "a": (403, {}, b"")})
    for _ in range(2):
        with pytest.raises(Exception):
            client.get(BASE + "a")
    with pytest.raises(SessionProblem):
        client.get(BASE + "a")


def test_delay_floor():
    with pytest.raises(ValueError):
        PoliteClient(min_delay=1.0)


def test_refuses_robots_disallowed(make_client):
    with pytest.raises(Exception):
        make_client().get(BASE + "cnstats/index.php")


def test_cookie_formats(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("# Netscape HTTP Cookie File\n#HttpOnly_www.russian-records.com\tFALSE\t/\tTRUE\t0\tsessionid\tabc\n"
                 ".example.com\tTRUE\t/\tFALSE\t0\tother\tz\n", encoding="utf-8")
    jar = load_cookies(str(f))
    assert jar.get("sessionid") == "abc" and jar.get("other") is None
    f.write_text('[{"name":"sessionid","value":"q","domain":".russian-records.com"}]', encoding="utf-8")
    assert load_cookies(str(f)).get("sessionid") == "q"
    assert load_cookies(cookie_header="Cookie: sessionid=r; 4images_userid=-1").get("4images_userid") == "-1"
    assert load_cookies(session_id="s").get("sessionid") == "s"


def test_rejects_html_as_audio(tmp_path):
    p = tmp_path / "x.part"
    p.write_bytes(b"<html>error</html>")
    with pytest.raises(InvalidMedia):
        sniff_audio(p)


# --- end to end ----------------------------------------------------------------------

def test_end_to_end_test_mode(tmp_path, make_client):
    client = make_client()
    state = State(tmp_path / "archive_state.db")
    crawler = Crawler(client, state, tmp_path / "archive", cache_dir=tmp_path / "cache")
    crawler.seed()
    crawler.run(limit=5, test_mode=True, page_budget=80)
    assert crawler.completed_this_run == 5
    assert not any("cnstats" in u for u in client.request_log)

    rows = {r["item_id"]: r for r in state.conn.execute("SELECT * FROM tracks")}
    done = {k: r for k, r in rows.items() if r["status"] == "COMPLETED"}
    assert len(done) == 5
    # one item per category before a second from the same category
    assert "106" not in done

    root = tmp_path / "archive"
    rel = {k: str((__import__("pathlib").Path(r["local_filepath"])).relative_to(root)).replace("\\", "/")
           for k, r in done.items()}
    # {Category} = "Top - Leaf" (CATEGORY_MODE="top_leaf"), title from font.section-title
    stem101 = "Russian/Русские пластинки - Русские романсы/Тестовый Певец/1912 - Evening bells (Вечерний звон), romance [101]"
    assert rel["101"] == stem101 + ".mp3"
    assert rel["102"].startswith("Ukrainian/Украинские песни/Тестова Співачка/Unknown_Year - ")
    assert rel["103"].startswith("Instrumental/Марши и оркестры/Духовой оркестр/1910 - ")
    assert rel["104"].startswith("Radio_and_Speech/Russian/Радиозаписи/Тестовый Диктор/1941 - ")
    assert rel["105"].startswith("Newspapers_and_Print/Russian/Газеты/1913 - ") and rel["105"].endswith(".jpg")

    tags = ID3(done["101"]["local_filepath"])
    assert tags.version[:2] == (2, 4)
    assert str(tags["TLAN"]) == "rus"
    assert str(tags["TPE1"]) == "Тестовый Певец" and str(tags["TCOM"]) == "Тестовый Композитор"
    assert str(tags["TDRC"]) == "1912"
    assert str(tags["TPUB"]) == "Граммофон, cat. 22-12345"
    assert "details.php?image_id=101" in str(tags.getall("COMM")[0])
    assert tags.getall("APIC") and tags.getall("APIC")[0].data[:3] == b"\xff\xd8\xff"
    assert str(ID3(done["102"]["local_filepath"])["TLAN"]) == "ukr"
    assert str(ID3(done["103"]["local_filepath"])["TLAN"]) == "zxx"
    assert done["101"]["language_detection_method"] == "EXPLICIT_METADATA"  # 'russian' field
    assert done["103"]["language_detection_method"] == "INSTRUMENTAL"  # beats the 'russian' field
    assert done["101"]["catalog_number"] == "22-12345" and done["101"]["matrix_number"] == "1234ab"
    comm = str(ID3(done["101"]["local_filepath"]).getall("COMM")[0])
    assert "Вечерній звонъ." in comm and "Added by: tester" in comm and "Hits" not in comm
    label = root / (stem101 + " (label).jpg")
    assert label.exists()
    assert json.loads(done["101"]["label_local_paths"]) == [str(label)]
    assert len(done["101"]["file_sha256"]) == 64
    assert not list(root.rglob("*.part"))

    # Resume: nothing re-downloaded, remaining item picked up.
    before = len(client.request_log)
    crawler2 = Crawler(client, state, root, cache_dir=None)
    crawler2.run()
    downloaded_again = [u for u in client.request_log[before:] if "zvon.mp3" in u]
    assert downloaded_again == []
    assert state.get_track("106")["status"] == "COMPLETED"
    state.close()


def test_download_removes_part_when_stream_breaks_mid_transfer(tmp_path, monkeypatch):
    """A connection cut mid-stream must not leave a .part behind: for an optional
    label scan on an item that still completes, nothing would ever retry it."""
    import requests

    class BrokenStream:
        headers = {"Content-Type": "image/jpeg", "Content-Length": "999999"}

        def iter_content(self, chunk):
            yield b"\xff\xd8\xff" + b"x" * 1000
            raise requests.exceptions.ChunkedEncodingError("Connection broken: IncompleteRead")

        def close(self):
            pass

    client = PoliteClient(sleep=lambda s: None)
    monkeypatch.setattr(client, "get", lambda url, stream=False: BrokenStream())
    dest = tmp_path / "1950 - Song [1].img.part"
    with pytest.raises(requests.exceptions.ChunkedEncodingError):
        client.download(BASE + "data/media/1/song.jpg", dest)
    assert not dest.exists()
