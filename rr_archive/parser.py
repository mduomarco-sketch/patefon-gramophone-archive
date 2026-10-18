"""4images page parsers (index.php, categories.php, details.php).

The live site sits behind a human CAPTCHA, so these selectors are written
against the stock 4images template structure with layered fallbacks. Every
parse records which rule fired in `notes` so that `scraper.py --recon` can
show exactly what matched on real pages and what needs adjusting.

Stock 4images conventions relied on:
  * breadcrumbs ("clickstream"): links with class="clickstream", Home first;
  * categories:  categories.php?cat_id=N[&page=M];
  * items:       details.php?image_id=N;
  * media files: data/media/<cat_id>/<file>, thumbnails data/thumbnails/...;
  * details info table: <tr><td><b>Label:</b></td><td>value</td></tr>.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, NavigableString, Tag

from . import config

AUDIO_EXT = (".mp3", ".ogg", ".oga", ".flac", ".wav", ".m4a", ".wma", ".aac", ".opus")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp")

# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

_KEEP_PARAMS = {
    "categories.php": ("cat_id", "page"),
    "details.php": ("image_id",),
    "download.php": ("image_id",),
    "index.php": (),
}


def canonical_url(url: str, base: str = config.BASE_URL) -> str:
    """Absolutize, force the canonical host, drop fragments and session/sort
    params so the same page is never queued twice."""
    absolute = urljoin(base, url.strip())
    parts = urlsplit(absolute)
    host = (parts.hostname or "").lower()
    if host in config.ALLOWED_HOSTS:
        host = config.CANONICAL_HOST
    script = parts.path.rsplit("/", 1)[-1].lower()
    query = parts.query
    if script in _KEEP_PARAMS:
        qs = parse_qs(parts.query)
        kept = [(k, qs[k][0]) for k in _KEEP_PARAMS[script] if k in qs and qs[k][0]]
        if script == "categories.php":
            kept = [(k, v) for k, v in kept if not (k == "page" and v == "1")]
        query = urlencode(kept)
    path = parts.path or "/"
    if path == "/":
        path = "/index.php"
    return urlunsplit(("https", host, path, query, ""))


def page_kind(url: str) -> str | None:
    parts = urlsplit(url)
    script = parts.path.rsplit("/", 1)[-1].lower()
    qs = parse_qs(parts.query)
    if script in ("", "index.php"):
        return "index"
    if script == "categories.php" and "cat_id" in qs:
        return "category"
    if script == "details.php" and "image_id" in qs:
        return "details"
    return None


def query_param(url: str, key: str) -> str | None:
    vals = parse_qs(urlsplit(url).query).get(key)
    return vals[0] if vals else None


def _ext(url: str) -> str:
    path = unquote(urlsplit(url).path).lower()
    dot = path.rfind(".")
    return path[dot:] if dot != -1 else ""


def _same_site(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() in config.ALLOWED_HOSTS


def _text(node) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Crumb:
    cat_id: str | None
    name: str
    url: str | None = None


@dataclass
class ListingPage:
    url: str
    cat_id: str | None
    name: str
    breadcrumbs: list[Crumb]
    subcategories: list[tuple[str, str, str]] = field(default_factory=list)  # (cat_id, name, url)
    items: list[tuple[str, str, str]] = field(default_factory=list)          # (image_id, name, url)
    pages: list[str] = field(default_factory=list)                            # pagination URLs
    notes: list[str] = field(default_factory=list)
    audio_items: set[str] = field(default_factory=set)  # rows with a data-src play button


@dataclass
class ItemPage:
    url: str
    image_id: str | None
    title: str
    breadcrumbs: list[Crumb]
    fields: dict[str, str]              # canonical key -> value
    raw_fields: dict[str, str]          # label as printed -> value
    description: str
    keywords: str
    audio_urls: list[str]
    image_urls: list[str]
    download_url: str | None
    linked_items: list[str]
    page_title: str
    notes: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)   # catalog table, has_pdf, ...

    @property
    def cat_id(self) -> str | None:
        for c in reversed(self.breadcrumbs):
            if c.cat_id:
                return c.cat_id
        return None


# ---------------------------------------------------------------------------
# Field label aliases (English + Russian 4images language packs and the
# custom fields a record archive is likely to define).
# ---------------------------------------------------------------------------

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    # russian-records.com labels first (verified in _recon/), then generic 4images/Russian ones.
    "title": ("title name", "name", "название", "title", "наименование", "song", "песня",
              "произведение"),
    "original_title": ("оригинальное название", "original title", "название на языке оригинала"),
    "artist": ("artist(s)", "исполнитель(и)", "исполнитель", "исполнители", "performer",
               "performers", "artist", "artists", "singer", "певец", "певица", "vocal", "vocals",
               "вокал", "поет", "исполняет"),
    "composer": ("composer", "композитор", "музыка", "music", "муз", "автор музыки"),
    "lyricist": ("lyrics by", "автор слов", "слова", "lyrics", "words", "сл", "текст", "lyricist"),
    "conductor": ("bandmaster or conductor", "дирижер", "дирижер или капельмейстер", "conductor"),
    "year": ("recording date", "дата записи", "год", "year", "год записи", "recorded",
             "date of recording", "год выпуска", "release year", "запись"),
    "recording_place": ("recording place", "место записи"),
    "label": ("label(s)", "фирма(ы)", "фирма", "лейбл", "label", "record label",
              "фирма грамзаписи", "завод", "издатель", "publisher", "марка", "фирма-изготовитель"),
    "catalog": ("catalog no", "номер", "каталожный номер", "catalog", "catalogue", "cat no",
                "cat number", "кат номер", "номер пластинки", "record number", "catalog number",
                "catalogue number", "№ пластинки", "номер по каталогу"),
    "matrix": ("mx/ctr no", "matrix", "матрица", "номер матрицы", "matrix no", "matrix number",
               "мат"),
    "mirror_transcription": ("mirror transcription", "зеркальная транскрипция"),
    "language": ("language(s) or ethnics", "язык(и) или этнос", "язык", "language",
                 "язык исполнения"),
    "catalog_category": ("catalog category", "каталожная категория"),
    "accompaniment": ("accompaniment type", "аккомпанемент", "accompaniment", "сопровождение",
                      "в сопровождении"),
    "genre": ("genre (music category)", "жанр (музыкальная категория)", "жанр", "genre"),
    "speed": ("speed of transfer", "скорость оцифровки"),
    "record_size": ("record size", "размер пластинки"),
    "label_size": ("label size", "размер этикетки"),
    "notes": ("additional information", "дополнительная информация"),
    "work": ("larger composition name", "название крупной формы", "из произведения"),
    "description": ("описание", "description", "комментарий", "comment", "примечание"),
    "keywords": ("additional keywords", "дополнительные ключевые слова", "ключевые слова",
                 "keywords"),
    "upload_date": ("дата", "date", "added", "дата добавления"),
}
IGNORED_LABELS = {"hits", "просмотров", "downloads", "загрузок", "rating", "рейтинг",
                  "file size", "размер файла", "added by", "добавил", "comments",
                  "комментарии", "голосов", "votes", "размер", "size", "разрешение",
                  "label file size", "label downloads", "audio downloads", "pdf file",
                  "last updated by", "size of file mp3"}

_ALIAS_LOOKUP = {alias: key for key, aliases in FIELD_ALIASES.items() for alias in aliases}


def normalize_label(label: str) -> str:
    s = label.lower().replace("ё", "е").replace("\xa0", " ")
    s = re.sub(r"[:.\s]+$", "", s)
    s = s.replace(".", " ")
    return re.sub(r"\s+", " ", s).strip()


def notes_fields(raw_fields: dict[str, str]) -> list[tuple[str, str]]:
    """Rows worth keeping in COMM/notes: recognised metadata plus the
    uploader credit, never page counters or comment widgets."""
    out = []
    for label, value in raw_fields.items():
        if not value:
            continue
        key = canonical_field(label)
        if (key and key not in ("description", "title")) or normalize_label(label) == "added by":
            out.append((label, value))
    return out


def canonical_field(label: str) -> str | None:
    norm = normalize_label(label)
    if norm in IGNORED_LABELS:
        return None
    return _ALIAS_LOOKUP.get(norm)


# ---------------------------------------------------------------------------
# Breadcrumbs
# ---------------------------------------------------------------------------

_SEP_RE = re.compile(r"^[\s/»>|:\-–—\xa0]+|[\s/»>|:\-–—\xa0]+$")


def parse_breadcrumbs(soup: BeautifulSoup, base: str, notes: list[str]) -> tuple[list[Crumb], str]:
    """Return (crumbs, trailing_text). trailing_text is the current page's own
    name when 4images renders it as plain text after the last link."""
    container = None
    for node in soup.select(".clickstream"):
        if node.name != "a" and node.find("a"):
            container = node
            break
    if container is None:
        link = soup.select_one("a.clickstream")
        if link is not None:
            container = link.parent
            notes.append("breadcrumbs: parent of a.clickstream")
    else:
        notes.append("breadcrumbs: .clickstream container")
    if container is None:
        # Fallback: the parent of the first categories.php link that also
        # holds a link back to index.php.
        for a in soup.find_all("a", href=True):
            if "categories.php" in a["href"] and a.parent is not None:
                parent = a.parent
                if any("index.php" in (x.get("href") or "") for x in parent.find_all("a", href=True)):
                    container = parent
                    notes.append("breadcrumbs: heuristic parent of index+categories links")
                    break
    if container is None:
        notes.append("breadcrumbs: NOT FOUND")
        return [], ""

    crumbs: list[Crumb] = []
    trailing = []
    for child in container.children:
        if isinstance(child, Tag) and child.name == "a" and child.get("href"):
            trailing = []
            href = canonical_url(child["href"], base)
            kind = page_kind(href)
            name = _text(child)
            if not re.search(r"\w", name):
                # russian-records.com abbreviates deep crumbs to ". ." and keeps
                # the real name in title="..."
                name = (child.get("title") or "").strip()
            if kind == "index":
                continue  # "Home"
            crumbs.append(Crumb(query_param(href, "cat_id") if kind == "category" else None, name, href))
        elif isinstance(child, NavigableString):
            trailing.append(str(child))
        elif isinstance(child, Tag):
            trailing.append(child.get_text(" "))
    tail = _SEP_RE.sub("", re.sub(r"\s+", " ", "".join(trailing))).strip()
    return [c for c in crumbs if c.cat_id], tail


# ---------------------------------------------------------------------------
# Listing pages (index.php / categories.php)
# ---------------------------------------------------------------------------

_SHOWNOTE_RE = re.compile(r"shownote\('((?:[^'\\]|\\.)*)'")
_UPLOADER_SUFFIX_RE = re.compile(r"\s*\([^()]*\)\s*$")


_AUDIO_SRC_RE = re.compile(r"\.(?:mp3|ogg|oga|flac|wav|m4a|wma|aac|opus)$", re.I)


def _listing_label(a: Tag) -> str:
    """Thumbnail links carry their title only in onmouseover="shownote('Title
    (Original), form (uploader)', ...)" on russian-records.com."""
    label = _text(a)
    if label:
        return label
    m = _SHOWNOTE_RE.search(a.get("onmouseover") or "")
    if m:
        return _UPLOADER_SUFFIX_RE.sub("", m.group(1)).strip()
    img = a.find("img")
    return img.get("alt", "").strip() if img else ""


def parse_listing(html: str, url: str) -> ListingPage:
    soup = BeautifulSoup(html, "lxml")
    url = canonical_url(url)
    notes: list[str] = []
    crumbs, tail = parse_breadcrumbs(soup, url, notes)
    cat_id = query_param(url, "cat_id")
    name = tail or (crumbs[-1].name if crumbs else "")
    page = ListingPage(url=url, cat_id=cat_id, name=name, breadcrumbs=crumbs, notes=notes)

    seen_cats, seen_items, seen_pages = set(), set(), set()
    crumb_ids = {c.cat_id for c in crumbs}
    for a in soup.find_all("a", href=True):
        href = canonical_url(a["href"], url)
        if not _same_site(href):
            continue
        kind = page_kind(href)
        if kind == "category":
            cid = query_param(href, "cat_id")
            if cid == cat_id:
                pg = query_param(href, "page")
                if pg and href not in seen_pages and href != url:
                    seen_pages.add(href)
                    page.pages.append(href)
                continue
            if query_param(href, "page"):
                continue  # pagination of a different category; reached via that category
            if cid in crumb_ids or cid in seen_cats:
                continue
            label = _text(a)
            if not label and a.find("img"):
                continue  # icon link duplicate of a text link
            seen_cats.add(cid)
            page.subcategories.append((cid, label, href))
        elif kind == "details":
            iid = query_param(href, "image_id")
            label = _listing_label(a)
            if iid in seen_items:
                # prefer a text label over an empty thumbnail link
                if label:
                    page.items = [(i, n or label, u) if i == iid else (i, n, u) for i, n, u in page.items]
                continue
            seen_items.add(iid)
            page.items.append((iid, label, href))
            tr = a.find_parent("tr")
            if tr is not None and tr.find(attrs={"data-src": _AUDIO_SRC_RE}):
                page.audio_items.add(iid)
    notes.append(f"listing: {len(page.subcategories)} subcats, {len(page.items)} items "
                 f"({len(page.audio_items)} with audio), {len(page.pages)} extra pages")
    return page


# ---------------------------------------------------------------------------
# Details pages
# ---------------------------------------------------------------------------

_SAVED_FROM_RE = re.compile(r"saved from url=\(\d+\)(\S+)", re.I)
_ID_PATTERNS = (
    re.compile(r"download\.php\?[^\"'>]*?image_id=(\d+)", re.I),
    re.compile(r"postcards\.php\?[^\"'>]*?image_id=(\d+)", re.I),
    re.compile(r"addfavorites[^\"'>]*?[?&](?:id|image_id)=(\d+)", re.I),
    re.compile(r"""name=["']?image_id["']?\s+value=["']?(\d+)""", re.I),
)
_FLASHVAR_RE = re.compile(r"(?:file|soundfile|mp3|url|src)=([^&\"'\s]+)", re.I)
_QUOTED_MEDIA_RE = re.compile(
    r"""["'(=]([^"'()\s<>?&=]+?\.(?:mp3|ogg|oga|flac|wav|m4a|wma|aac|opus))(?:\?[^"'\s<>]*)?["')&\s]""", re.I)
_KV_LINE_RE = re.compile(r"^\s*([^:]{2,40}?)\s*:\s*(.+?)\s*$")


def source_url_hint(html: str) -> str | None:
    """URL of a browser-saved page (Chrome/Edge 'saved from' comment or
    <link rel=canonical>/og:url)."""
    m = _SAVED_FROM_RE.search(html[:3000])
    if m:
        return m.group(1)
    soup = BeautifulSoup(html[:20000], "lxml")
    link = soup.find("link", rel="canonical")
    if link and link.get("href"):
        return link["href"]
    og = soup.find("meta", property="og:url")
    if og and og.get("content"):
        return og["content"]
    return None


def detect_image_id(html: str) -> str | None:
    hint = source_url_hint(html)
    if hint and page_kind(canonical_url(hint)) == "details":
        return query_param(canonical_url(hint), "image_id")
    for rx in _ID_PATTERNS:
        m = rx.search(html)
        if m:
            return m.group(1)
    return None


_LABEL_CELL_RE = re.compile(r"^[^\W\d_][\w\s()/&.,'’\-]{0,58}:$")


def _info_rows(soup: BeautifulSoup) -> list[tuple[str, Tag, Tag]]:
    """(label, value_cell, tr) for every "Label: | value" row. russian-records.com
    rows can have a third cell (reverse-side thumbnail), so >= 2 cells."""
    rows = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all(["td", "th"], recursive=False)
        if len(tds) < 2:
            continue
        label = _text(tds[0])
        if _LABEL_CELL_RE.match(label):
            rows.append((label, tds[1], tr))
    return rows


def _split_subfields(cell: Tag) -> list[tuple[str | None, str]]:
    """'russian | <b>Catalog category:</b> Orchestra | <b>Genre:</b> Waltz'
    -> [(None, 'russian'), ('Catalog category', 'Orchestra'), ('Genre', 'Waltz')]"""
    segs: list[list] = [[None, []]]
    for node in cell.children:
        if isinstance(node, Tag) and node.name in ("b", "strong"):
            t = _text(node)
            if t.endswith(":") and 1 < len(t) <= 60:
                segs.append([t[:-1].strip(), []])
                continue
        segs[-1][1].append(node.get_text(" ") if isinstance(node, Tag) else str(node))
    out = []
    for label, parts in segs:
        text = re.sub(r"\s+", " ", "".join(parts).replace("\xa0", " ")).strip(" |\t\n")
        out.append((label, text.strip()))
    return out


def _cell_lines(cell: Tag) -> list[str]:
    parts = []
    for br in cell.find_all("br"):
        br.replace_with("\n")
    for line in cell.get_text("").splitlines():
        line = re.sub(r"[ \t\xa0]+", " ", line).strip()
        if line:
            parts.append(line)
    return parts


def _own_rows(table: Tag) -> list[Tag]:
    return [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]


def _label_transcription(anchor_tr: Tag | None) -> list[str]:
    """The unlabelled first row of the info table: on records the text printed
    on the label (often several languages, original spelling); on books and
    periodicals a bibliographic note."""
    if anchor_tr is None:
        return []
    table = anchor_tr.find_parent("table")
    if table is None:
        return []
    rows = _own_rows(table)
    if not rows:
        return []
    tds = rows[0].find_all("td", recursive=False)
    if len(tds) < 2 or _LABEL_CELL_RE.match(_text(tds[0])):
        return []
    inner = tds[1].find("td") or tds[1]
    return _cell_lines(copy.copy(inner))


def _catalog_table(soup: BeautifulSoup) -> dict:
    """Label | Catalog No | Mx/Ctr No | Take | Order No | Censorial No table;
    first data row = this side, second = reverse side."""
    for td in soup.find_all("td"):
        if normalize_label(_text(td)) != "catalog no":
            continue
        header_tr = td.find_parent("tr")
        table = header_tr.find_parent("table") if header_tr else None
        if table is None:
            continue
        headers = [normalize_label(_text(c)) for c in header_tr.find_all("td", recursive=False)]
        rows = _own_rows(table)
        start = rows.index(header_tr) + 1
        result: dict = {}
        for side, tr in zip(("this_side", "reverse_side"), rows[start:start + 2]):
            cells = dict(zip(headers, tr.find_all("td", recursive=False)))
            result[side] = {h: _text(c) for h, c in cells.items() if _text(c)}
            if side == "this_side" and "label" in cells:
                a = cells["label"].find("a")
                result["label_name"] = (a.get("title") or _text(a)) if a else _text(cells["label"])
        return result
    return {}


_NON_LATIN_RE = re.compile(r"[Ѐ-ԯ֐-׿Ⴀ-ჿ԰-֏]")


def _native_text(title: str, transcription: list[str]) -> str:
    """Original-script text for language detection: parenthesised parts of the
    bilingual title plus non-Latin lines of the label transcription."""
    parts = [p for p in re.findall(r"\(([^()]+)\)", title) if _NON_LATIN_RE.search(p)]
    parts += [line for line in transcription if _NON_LATIN_RE.search(line)]
    return " ".join(dict.fromkeys(parts))


def _collect_media(soup: BeautifulSoup, html: str, url: str, image_id: str | None,
                   desc_cell: Tag | None) -> tuple[list[str], list[str], str | None]:
    audio: list[str] = []
    images: list[str] = []
    download_url = None

    def add(lst, raw):
        if not raw:
            return
        u = canonical_url(unquote(raw) if "%2F" in raw.upper() else raw, url)
        if _same_site(u) and u not in lst:
            lst.append(u)

    def points_elsewhere(tag: Tag) -> bool:
        a = tag if tag.name == "a" and tag.get("href") else tag.find_parent("a", href=True)
        if a is None:
            return False
        href = canonical_url(a["href"], url)
        return page_kind(href) == "details" and query_param(href, "image_id") != image_id

    # 1. The page's own player: <audio src> / <source src>.
    for tag in soup.find_all(["audio", "source"]):
        if tag.get("src") and _ext(urljoin(url, tag["src"])) in AUDIO_EXT:
            add(audio, tag["src"])

    for tag in soup.find_all(True):
        for attr in ("href", "src", "value", "data", "data-src", "data-audio", "data-url", "flashvars"):
            val = tag.get(attr)
            if not val or not isinstance(val, str):
                continue
            candidates = [val]
            if attr in ("flashvars", "value", "src", "data") and "=" in val:
                candidates += _FLASHVAR_RE.findall(val)
            for cand in candidates:
                ext = _ext(urljoin(url, cand))
                if ext in AUDIO_EXT:
                    # 2. Other players (data-src="...mp3"), unless they sit in a
                    #    thumbnail that links to a different item.
                    if not points_elsewhere(tag) and not _inside_other_item(tag, url, image_id):
                        add(audio, cand)
                elif ext in IMAGE_EXT and tag.name in ("img", "a", "input") and not points_elsewhere(tag):
                    lowered = urljoin(url, cand).lower()
                    if any(x in lowered for x in ("/templates/", "/icons/", "/userpic/", "/favicon")):
                        continue
                    if re.search(r"_qr\.\w+$", lowered):
                        continue  # QR code of the page itself
                    in_desc = desc_cell is not None and desc_cell in tag.parents
                    if "/data/" in lowered or in_desc:
                        add(images, cand)
            if attr == "href" and "download.php" in val and "image_id=" in val:
                download_url = canonical_url(val, url)
    if not audio:
        # 3. Last resort: media paths inside scripts.
        for m in _QUOTED_MEDIA_RE.finditer(html):
            add(audio, m.group(1))

    audio.sort(key=lambda u: (0 if "/data/media/" in u else 1))
    # Full-size scans live in data/media; thumbnails have different names
    # (876.jpg vs 876_2.jpg), so drop thumbnails whenever a full-size exists.
    if any("/data/media/" in u for u in images):
        images = [u for u in images if "/data/thumbnails/" not in u]
    images.sort(key=lambda u: (0 if "/data/media/" in u else 1 if "/data/thumbnails/" not in u else 2))
    return audio, images[: config.MAX_LABEL_IMAGES], download_url


def _inside_other_item(tag: Tag, url: str, image_id: str | None) -> bool:
    """Listing/nav markup nests the play icon next to (not inside) the item's
    <a href=details...>, so look at the enclosing table cell too."""
    cell = tag.find_parent("td")
    if cell is None:
        return False
    for a in cell.find_all("a", href=True):
        href = canonical_url(a["href"], url)
        if page_kind(href) == "details" and query_param(href, "image_id") != image_id:
            return True
    return False


_SECTION_TITLE_SKIP = {"details", "post comment", "comments", "подробности", "комментарии",
                       "добавить комментарий"}


def parse_details(html: str, url: str | None = None) -> ItemPage:
    soup = BeautifulSoup(html, "lxml")
    notes: list[str] = []
    image_id = query_param(canonical_url(url), "image_id") if url else None
    if not image_id:
        image_id = detect_image_id(html)
        notes.append(f"image_id from page content: {image_id}")
    if not url:
        url = config.BASE_URL + f"details.php?image_id={image_id}" if image_id else config.START_URL
    url = canonical_url(url)

    crumbs, tail = parse_breadcrumbs(soup, url, notes)

    raw_fields: dict[str, str] = {}
    fields: dict[str, str] = {}
    desc_cell = None
    anchor_tr = None
    for label, cell, tr in _info_rows(soup):
        for sub_label, value in _split_subfields(cell):
            name = (sub_label or label).rstrip(":").strip()
            key = canonical_field(name)
            if key in ("title", "language") and anchor_tr is None:
                anchor_tr = tr
            if key == "notes" and sub_label is None:
                # liner notes: keep the archive's own line breaks (verse or prose)
                value = "\n".join(_cell_lines(copy.copy(cell))) or value
            raw_fields.setdefault(name, value)
            if key == "description" and desc_cell is None:
                desc_cell = cell
            if key and value and key not in fields:
                fields[key] = value
    notes.append(f"info table: {len(raw_fields)} rows, mapped {sorted(fields)}")

    transcription = _label_transcription(anchor_tr)
    if transcription:
        notes.append(f"label transcription: {len(transcription)} lines")
    catalog = _catalog_table(soup)
    extra: dict = {}
    if catalog:
        this = catalog.get("this_side", {})
        for key, col in (("catalog", "catalog no"), ("matrix", "mx/ctr no")):
            if this.get(col):
                fields[key] = this[col]
        if catalog.get("label_name"):
            fields["label"] = catalog["label_name"]
        extra["catalog_table"] = catalog
        notes.append("catalog table: " + ", ".join(f"{k}={v}" for k, v in this.items()))
    # Books/periodicals: onclick="openTab('./view_pdf.php?image_id=N&image=2')".
    # view_pdf.php streams application/pdf directly (verified 2026-10-17).
    pdf_urls: list[str] = []
    for m in re.finditer(r"view_pdf\.php\?[^'\"\s)<>]+", html):
        u = canonical_url(m.group(0).replace("&amp;", "&"), url)
        if query_param(u, "image_id") in (image_id, None) and u not in pdf_urls:
            pdf_urls.append(u)
    if pdf_urls:
        extra["has_pdf"] = True
        extra["pdf_urls"] = pdf_urls
        notes.append(f"PDF scan available: {pdf_urls[0]}")

    description = "\n".join(transcription) or fields.get("description", "")
    keywords = fields.get("keywords", "")
    audio, images, download_url = _collect_media(soup, html, url, image_id, desc_cell)

    # "Key: value" lines inside a description (generic 4images custom metadata).
    if desc_cell is not None:
        for line in _cell_lines(copy.copy(desc_cell)):
            m = _KV_LINE_RE.match(line)
            if m:
                key = canonical_field(m.group(1))
                if key and key not in ("description", "upload_date") and key not in fields:
                    fields[key] = m.group(2)
                    notes.append(f"field '{key}' from description line")
        if not transcription:
            description = "\n".join(_cell_lines(copy.copy(desc_cell))) or description

    section = next((_text(n) for n in soup.select("font.section-title, .section-title")
                    if _text(n).lower() not in _SECTION_TITLE_SKIP), "")
    title_node = soup.select_one(".title")
    title = section or fields.get("title") or _text(title_node) or tail
    if not title:
        t = _text(soup.find("title"))
        title = re.split(r"\s+[-|»]\s+", t)[-1] if t else ""
        notes.append("title: fallback from <title>")
    elif section:
        notes.append("title: font.section-title")
    if "original_title" not in fields:
        native = _native_text(title, transcription)
        if native:
            fields["original_title"] = native

    linked = []
    for a in soup.find_all("a", href=True):
        href = canonical_url(a["href"], url)
        if page_kind(href) == "details" and query_param(href, "image_id") != image_id and href not in linked:
            linked.append(href)

    if not audio:
        notes.append("audio: none found")
    return ItemPage(url=url, image_id=image_id, title=title, breadcrumbs=crumbs, fields=fields,
                    raw_fields=raw_fields, description=description, keywords=keywords,
                    audio_urls=audio, image_urls=images, download_url=download_url,
                    linked_items=linked, page_title=_text(soup.find("title")), notes=notes,
                    extra=extra)


def looks_like_details(html: str) -> bool:
    return bool(detect_image_id(html)) and ("details.php" in html or "download.php" in html
                                            or "class=\"title\"" in html)
