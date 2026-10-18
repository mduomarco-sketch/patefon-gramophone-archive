"""Synthetic fixtures mirroring the real russian-records.com 4images markup
(verified against _recon/ on 2026-10-17): '>'-separated clickstream with
'. .' abbreviations + title attributes, font.section-title headings, packed
'value | <b>Sub:</b> value' info rows, the Label/Catalog No/Mx/Ctr No table,
the unlabelled label-transcription row, <audio src>, navigation thumbnails
with data-src players, userpic/QR images, shownote() listing rows, PDF scans.

All titles, names and values are invented for testing."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = "https://www.russian-records.com/"


def silent_mp3(frames: int = 60) -> bytes:
    """MPEG-1 Layer III, 128 kbps, 44.1 kHz, no padding -> 417-byte frames."""
    frame = b"\xff\xfb\x90\x64" + b"\x00" * 413
    return frame * frames


def tiny_jpeg() -> bytes:
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 64 + b"\xff\xd9"


def page(body: str, title: str = "Russian Records") -> bytes:
    html = f"""<html><head>
<meta http-equiv="content-type" content="text/html; charset=windows-1251" />
<title>{title}</title></head><body>
<table><tr><td><img src="./templates/white/images/russian-records-logo.gif"></td></tr></table>
{body}
<img src="./data/userpic/1.jpg">
</body></html>"""
    return html.encode("cp1251", errors="xmlcharrefreplace")


def clickstream(*crumbs, tail=""):
    """crumbs: (cat_id, name) or (cat_id, name, abbreviated) - deep levels are
    rendered as '. .' with the real name in title=, like the live site."""
    links = ['<a href="./index.php" class="clickstream">Home</a>']
    for c in crumbs:
        cid, name = c[0], c[1]
        if len(c) > 2 and c[2]:
            links.append(f'<a href="./categories.php?cat_id={cid}&amp;l=english" class="clickstream" '
                         f'title="{name}">. .</a>')
        else:
            links.append(f'<a href="./categories.php?cat_id={cid}" class="clickstream">{name}</a>')
    trail = " > ".join(links) + (f" > {tail}" if tail else "")
    return f'<p><b><span class="clickstream">{trail}</span></b></td><td width="45%">'


def row(label, value, cls="row1"):
    return (f'<tr>\n<td width="30%" valign="top" class="{cls}"><b>{label}:</b></td>\n'
            f'<td width="70%" valign="top" class="{cls}" colspan=2>{value}</td>\n</tr>')


def sub(label, value):
    return f' | <b>{label}:</b> {value}'


def listing_row(image_id, cat_id, title, uploader, mp3=None):
    play = (f'<a href="#" style="outline: none;" id="1" data-src="./data/media/{cat_id}/{mp3}" '
            f'class="playable selected"><img src=./templates/white/images/audio.gif></a>') if mp3 else ""
    return (f'<tr class="imagerow1"><td align="center"></td><td>'
            f'<a onmousemove="movenote()" onmouseover="shownote(\'{title} ({uploader})\','
            f'\'./data/thumbnails/{cat_id}/t{image_id}.jpg\',160,1,1)" '
            f'href="./details.php?image_id={image_id}" target=_blank>'
            f'<img src="./data/thumbnails/{cat_id}/t{image_id}.jpg"></a>{play}</td></tr>')


def details_page(image_id, crumbs, section_title, transcription, rows, *, media_html="",
                 catalog=None, nav_other=None, pdf=False, reverse_id=None):
    nav = ""
    if nav_other:
        oid, ocat = nav_other
        nav = (f'<table><tr><td><a onmouseover="shownote(\'Other item (x)\',\'./data/thumbnails/{ocat}/o.jpg\')" '
               f'href="./details.php?image_id={oid}#nav"><img src="./data/thumbnails/{ocat}/o.jpg"></a>'
               f'<a href="#" data-src="./data/media/{ocat}/other.mp3" class="playable"></a></td>'
               f'<td><img src="./data/thumbnails/{crumbs[-1][0]}/{image_id}_2.jpg"></td></tr></table>')
    trans = "<br />\n".join(transcription)
    reverse = (f'<td width="15%"><a href="./details.php?image_id={reverse_id}"><b>Reverse side</b></a></td>'
               if reverse_id else "")
    cat_tbl = ""
    if catalog:
        label_name, label_cat, catno, mx = catalog
        cat_tbl = f"""<tr><td colspan=3 class="rowspecial2">
<table width="100%" border="0" cellspacing="0" cellpadding="2">
<tr><td class="rowspecial2" colspan=8 align="middle"><b>Original matrix: {label_name} # {mx}</b></td></tr><tr>
<td width="31%" class="row2" align="middle" colspan=2><b>Label</b></td>
<td width="11%" class="row3" align="middle"><b>Catalog&nbsp;No</b></td>
<td width="11%" class="row2" align="middle"><b>Mx/Ctr&nbsp;No</b></td>
<td width="4%" class="row3" align="middle"><b>Take</b></td>
<td width="11%" class="row2" align="middle"><b>Order&nbsp;No</b></td>
<td width="11%" class="row3" align="middle"><b>Censorial&nbsp;No</b></td>
<td width="22%" class="row2" colspan=2 align="middle"><b>Additional information</b></td>
</tr><tr><td class="row1" align="left" colspan=2> <a title="{label_name}" href="./categories.php?cat_id={label_cat}">{label_name}</a></td>
<td class="row2" align="middle"><a href="./search.php?search_keywords={catno}">{catno}</a></td>
<td class="row1" align="middle"><a href="./search.php?search_keywords={mx}">{mx}</a></td>
<td class="row2"></td><td class="row1"></td><td class="row2"></td><td class="row1">Original matrix </td><td></td></tr>
<tr><td class="row2" colspan=2> Reverse Side</td><td class="row3">X-1</td><td class="row2">X-2</td>
<td></td><td></td><td></td><td> --//-- </td><td></td></tr>
</table></td></tr>"""
    pdf_html = (f'<a onclick="openTab(\'./view_pdf.php?image_id={image_id}&image=2\');">'
                f'<img src="./templates/white/icons/pdf_d.gif"></a>') if pdf else ""
    body = f"""{clickstream(*crumbs, tail=section_title.split(' (')[0])}
<table><tr><td colspan=3><font class="section-title">{section_title}</font></td></tr></table>
{nav}
<div align="center"><table><tr><td>{media_html}</td></tr></table></div>
{pdf_html}
<a href="./download.php?image_id={image_id}"><img src="./templates/white/images/download.gif"></a>
<p align="left"><font class="section-title">details</font>
<table width="756"><tr><td class="bordercolor"><div align="left">
<table width="100%" border="0" cellpadding="3" cellspacing="0">
<tr>
<td width="30%" valign="top" class="row1"><table><tr><td class="row1"><img src="./templates/white/images/fund_5.png" alt="Base fund"></td></tr></table></td>
<td valign="top" class="row1"><table><tr><td valign="top" class="row1">{trans}</td></tr>
<tr><td valign="bottom" class="row1"></td></tr></table></td>
{reverse}
</tr>
{row("Mirror Transcription", "TEST-" + str(image_id), "row2")}
{cat_tbl}
{''.join(rows)}
{row("Hits", "77 | <b>Label downloads:</b> 5 | <b>Audio downloads:</b> 3")}
{row("Added by", "tester | 01.01.2015 10:00 | <b>Last updated by:</b> tester2 | 02.02.2016 11:00", "row2")}
</table></div></td></tr></table>
<table><tr><td class="row1"><b>[ 1 ] 2 Next &raquo;&raquo; Go to page:</b></td><td class="row1">x</td></tr></table>
<img src="./data/thumbnails/{crumbs[-1][0]}/{image_id:06d}_qr.png">"""
    return page(body)


def audio_tag(cat, name, image_id):
    return f'<audio id="id_{image_id}_2" src="./data/media/{cat}/{name}.mp3" type="audio/mp3" controls="controls"></audio>'


def label_img(cat, name):
    return f'<img id="image" src="./data/media/{cat}/{name}.jpg" width="740">'


def build_site():
    s = {}
    s["index.php"] = page(f"""{clickstream()}
<a href="./categories.php?cat_id=10" class="maincat">Русские пластинки</a>
<a href="./categories.php?cat_id=2" class="maincat">Украинские песни</a>
<a href="./categories.php?cat_id=3" class="maincat">Марши и оркестры</a>
<a href="./categories.php?cat_id=4" class="maincat">Радиозаписи</a>
<a href="./categories.php?cat_id=5" class="maincat">Газеты</a>
<a href="./cnstats/index.php">stats</a>""")
    s["categories.php?cat_id=10"] = page(f"""{clickstream(tail="Русские пластинки")}
<a href="./categories.php?cat_id=1">Русские романсы</a>""")
    s["categories.php?cat_id=1"] = page(f"""{clickstream((10, "Русские пластинки"), tail="Русские романсы")}
<table>{listing_row(101, 1, "Evening bells (Вечерний звон), romance", "tester", "zvon.mp3")}</table>
<a href="./categories.php?cat_id=1&amp;page=2">2</a>""")
    s["categories.php?cat_id=1&page=2"] = page(f"""{clickstream((10, "Русские пластинки"), tail="Русские романсы")}
<table>{listing_row(106, 1, "Moon shine (Сіяніе луны)", "tester", "siyanie.mp3")}</table>""")
    s["categories.php?cat_id=2"] = page(f"""{clickstream(tail="Украинские песни")}
<table>{listing_row(102, 2, "Moonlit night (Ніч яка місячна), song", "tester", "nich.mp3")}</table>""")
    s["categories.php?cat_id=3"] = page(f"""{clickstream(tail="Марши и оркестры")}
<table>{listing_row(103, 3, "Test regiment march", "tester", "marsh.mp3")}</table>""")
    s["categories.php?cat_id=4"] = page(f"""{clickstream(tail="Радиозаписи")}
<table>{listing_row(104, 4, "Radio address", "tester", "speech.mp3")}</table>""")
    s["categories.php?cat_id=5"] = page(f"""{clickstream(tail="Газеты")}
<table>{listing_row(105, 5, "Test newspaper, 1913", "tester")}</table>""")

    russian_romances = [(10, "Русские пластинки", True), (1, "Русские романсы")]
    s["details.php?image_id=101"] = details_page(
        101, russian_romances, "Evening bells (Вечерний звон), romance",
        ["101", "Evening bells", "Вечерній звонъ.", "Романсъ."],
        [row("Title Name", "Evening bells"),
         row("Language(s) or Ethnics", '<span title="Русский"><font color="#0060A9">russian</font></span>'
             + sub("Catalog category", "Vocal") + sub("Genre&nbsp;(Music Category)", "Romance"), "row2"),
         row("Artist(s)", '<a href="./search.php?search_keywords=x">Тестовый</a> Певец'),
         row("Composer", "Тестовый Композитор", "row2"),
         row("Lyrics By", "Тестовый Поэт"),
         row("Accompaniment Type", "piano", "row2"),
         row("Bandmaster or conductor", ""),
         row("Recording&nbsp;Place", "Москва" + sub("Recording Date", "ок. 1912"), "row2"),
         row("Speed of transfer", "78 rpm"),
         row("Record size", "25 cm" + sub("Label size", "90 mm"), "row2"),
         row("Additional keywords", "")],
        media_html=audio_tag(1, "zvon", 101) + label_img(1, "zvon"),
        catalog=("Граммофон", 77, "22-12345", "1234ab"), nav_other=(106, 1), reverse_id=106)
    s["details.php?image_id=102"] = details_page(
        102, [(2, "Украинские песни")], "Moonlit night (Ніч яка місячна), song",
        ["102", "Ніч яка місячна."],
        [row("Title Name", "Moonlit night"),
         row("Language(s) or Ethnics", "ukrainian" + sub("Catalog category", "Vocal")),
         row("Artist(s)", "Тестова Співачка", "row2")],
        media_html=audio_tag(2, "nich", 102) + label_img(2, "nich"))
    s["details.php?image_id=103"] = details_page(
        103, [(3, "Марши и оркестры")], "Test regiment march (Марш тестового полка)",
        ["103", "Маршъ."],
        [row("Title Name", "Test regiment march"),
         row("Language(s) or Ethnics", "russian" + sub("Catalog category", "Orchestra")
             + sub("Genre (Music Category)", "March")),
         row("Artist(s)", "Духовой оркестр", "row2"),
         row("Recording Place", "Санкт-Петербург" + sub("Recording Date", "1910"))],
        media_html=audio_tag(3, "marsh", 103))
    s["details.php?image_id=104"] = details_page(
        104, [(4, "Радиозаписи")], "Radio address (Выступление по радио)",
        ["104"],
        [row("Title Name", "Radio address"),
         row("Language(s) or Ethnics", "russian" + sub("Catalog category", "Speech")),
         row("Artist(s)", "Тестовый Диктор", "row2"),
         row("Recording Place", "Москва" + sub("Recording Date", "1941"))],
        media_html=audio_tag(4, "speech", 104))
    s["details.php?image_id=105"] = details_page(
        105, [(5, "Газеты")], "Test newspaper (Газета Тестовая правда), 1913",
        ["Тестовая правда, 1913, № 1"],
        [row("Name", "Test newspaper"),
         row("Language(s) or Ethnics", "russian"),
         row("Recording Date", "1913", "row2")],
        media_html=label_img(5, "gazeta"), pdf=True)
    s["details.php?image_id=106"] = details_page(
        106, russian_romances, "Moon shine (Сіяніе луны)",
        ["106", "Сіяніе луны."],
        [row("Title Name", "Moon shine"),
         row("Artist(s)", "Другой Певец/Хор", "row2")],
        media_html=audio_tag(1, "siyanie", 106) + label_img(1, "siyanie"))

    site = {}
    for k, v in s.items():
        site[BASE + k] = (200, {"Content-Type": "text/html; charset=windows-1251"}, v)
    for path in ("data/media/1/zvon.mp3", "data/media/2/nich.mp3", "data/media/3/marsh.mp3",
                 "data/media/4/speech.mp3", "data/media/1/siyanie.mp3"):
        site[BASE + path] = (200, {"Content-Type": "audio/mpeg"}, silent_mp3())
    for path in ("data/media/1/zvon.jpg", "data/media/2/nich.jpg", "data/media/5/gazeta.jpg",
                 "data/media/1/siyanie.jpg"):
        site[BASE + path] = (200, {"Content-Type": "image/jpeg"}, tiny_jpeg())
    # view_pdf.php streams the PDF itself (live site: application/pdf, inline).
    site[BASE + "view_pdf.php?image_id=105&image=2"] = (
        200, {"Content-Type": "application/pdf",
              "Content-Disposition": 'inline; filename="./data/media/5/000105_b.pdf"'}, tiny_pdf())
    return site


def tiny_pdf() -> bytes:
    return (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")


class FakeAdapter(BaseAdapter):
    def __init__(self, site, log):
        super().__init__()
        self.site, self.log = site, log

    def send(self, request, **kwargs):
        self.log.append(request.url)
        url = request.url
        status, headers, body = self.site.get(url, (404, {"Content-Type": "text/html"}, b"<html>404</html>"))
        resp = requests.Response()
        resp.status_code = status
        resp.headers = CaseInsensitiveDict({**headers, "Content-Length": str(len(body))})
        resp.raw = io.BytesIO(body)
        resp.url = url
        resp.request = request
        resp.encoding = None
        return resp

    def close(self):
        pass


@pytest.fixture
def fake_site():
    return build_site()


@pytest.fixture
def make_client(fake_site):
    from rr_archive.net import PoliteClient

    def factory(site=None, sleeps=None):
        log: list[str] = []
        session = requests.Session()
        session.mount("https://", FakeAdapter(site if site is not None else fake_site, log))
        sleeps = sleeps if sleeps is not None else []
        client = PoliteClient(session=session, min_delay=0, max_delay=0, enforce_floor=False,
                              sleep=sleeps.append)
        client.request_log = log
        return client
    return factory


@pytest.fixture(autouse=True)
def isolated_control_dir(tmp_path, monkeypatch):
    """Keep dashboard control files/settings and recon output out of the project."""
    from rr_archive import config
    monkeypatch.setattr(config, "CONTROL_DIR", tmp_path / "_control")
    monkeypatch.setattr(config, "RECON_DIR", tmp_path / "_recon")


@pytest.fixture(autouse=True)
def isolated_side_files(tmp_path, monkeypatch):
    """The exporter also rebuilds notes/label-fit indexes and reads lyrics and
    curated playlists: point every one of them at tmp so tests can never
    overwrite the real files the live dashboard serves."""
    import rr_archive.export as export_mod
    import rr_archive.labelfit as lf
    import rr_archive.lyrics as lyr
    import rr_archive.notes as notes_mod
    side = tmp_path / "_side"
    side.mkdir()
    monkeypatch.setattr(notes_mod, "INDEX_PATH", side / "notes_index.json")
    monkeypatch.setattr(notes_mod.build_index, "__defaults__", (config_cache(), side / "notes_index.json"))
    monkeypatch.setattr(notes_mod.load_index, "__defaults__", (side / "notes_index.json",))
    monkeypatch.setattr(lf, "FIT_PATH", side / "label_fit.json")
    monkeypatch.setattr(lf.update_fits, "__defaults__", (side / "label_fit.json", None))
    monkeypatch.setattr(lf.load_fits, "__defaults__", (side / "label_fit.json",))
    monkeypatch.setattr(lyr, "INDEX_PATH", side / "lyrics_index.json")
    monkeypatch.setattr(lyr, "META_PATH", side / "lyrics_meta.json")
    monkeypatch.setattr(lyr, "LYRICS_DIR", side / "_lyrics")
    monkeypatch.setattr(export_mod, "USER_PLAYLISTS", side / "user_playlists.json")
    monkeypatch.setattr(export_mod.save_user_playlists, "__defaults__", (side / "user_playlists.json",))
    monkeypatch.setattr(export_mod.load_user_playlists, "__defaults__", (side / "user_playlists.json",))
    return side


def config_cache():
    from rr_archive import config
    return config.PAGE_CACHE_DIR


@pytest.fixture
def built_archive(tmp_path, make_client):
    """Run the fake-site crawl to completion; returns (db_path, archive_root)."""
    from rr_archive.crawler import Crawler
    from rr_archive.state import State
    state = State(tmp_path / "archive_state.db")
    crawler = Crawler(make_client(), state, tmp_path / "archive", cache_dir=None,
                      control_dir=tmp_path / "_control")
    crawler.seed()
    crawler.run()
    state.close()
    return tmp_path / "archive_state.db", tmp_path / "archive"
