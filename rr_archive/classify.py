"""Media-type and language classification.

Language tiers, in priority order:
  1. EXPLICIT_METADATA - a language field, category/breadcrumb name, a title
     abbreviation such as "(укр.)", or a description phrase like
     "на украинском языке" / "sung in Yiddish".
  2. INSTRUMENTAL      - instrumental markers and no vocal markers (music only).
  3. SCRIPT_NLP        - Unicode script, decisive orthography (pre-reform
     Russian ѣ/ѳ/ѵ/word-final ъ; Ukrainian ї/є/ґ; Belarusian ў ...), then
     lingua restricted to candidates the letters still allow.
  4. DEFAULT_CYRILLIC  - Cyrillic vocal text no detector could pin down -> Russian.
  5. UNKNOWN           - nothing usable -> Unknown_Language.

"Gypsy"/"Jewish" categories are only *weak* hints: цыганский романс is a
Russian-language genre, and much Jewish repertoire on 78s is in Russian.
They are applied only when the text is not confidently Russian.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

MUSIC, RADIO, PRINT, IMAGE, NONE = "MUSIC", "RADIO_SPEECH", "PRINT", "IMAGE", "NONE"

EXPLICIT, INSTRUMENTAL, SCRIPT_NLP, DEFAULT_CYRILLIC, UNKNOWN = (
    "EXPLICIT_METADATA", "INSTRUMENTAL", "SCRIPT_NLP", "DEFAULT_CYRILLIC", "UNKNOWN")

INSTRUMENTAL_LANG = "Instrumental"
UNKNOWN_LANG = "Unknown_Language"

# Folder name -> ISO 639-2 code for the ID3 TLAN frame.
ISO639_2 = {
    "Russian": "rus", "Ukrainian": "ukr", "Belarusian": "bel", "Yiddish": "yid",
    "Hebrew": "heb", "Georgian": "geo", "Armenian": "arm", "Polish": "pol",
    "Romani": "rom", "Tatar": "tat", "Azerbaijani": "aze", "Kazakh": "kaz",
    "Uzbek": "uzb", "Bashkir": "bak", "Chuvash": "chv", "Latvian": "lav",
    "Lithuanian": "lit", "Estonian": "est", "Finnish": "fin", "German": "ger",
    "French": "fre", "Italian": "ita", "English": "eng", "Spanish": "spa",
    "Bulgarian": "bul", "Serbian": "srp", "Croatian": "hrv", "Czech": "cze",
    "Slovak": "slo", "Romanian": "rum", "Greek": "gre", "Hungarian": "hun",
    "Latin": "lat", "Church_Slavonic": "chu", "Macedonian": "mac",
    "Mongolian": "mon", "Portuguese": "por", "Swedish": "swe", "Dutch": "dut",
    "Turkish": "tur", "Danish": "dan", "Norwegian": "nor",
    INSTRUMENTAL_LANG: "zxx", UNKNOWN_LANG: "und",
}

# (folder name, regex of stems). Russian stems match adjective forms
# (украинская/украинские/украинских ...).
STRONG_LANG_PATTERNS: list[tuple[str, str]] = [
    ("Ukrainian", r"украинск|малороссийск|малорусск|\bukrain|little[ -]russian"),
    ("Belarusian", r"белорусск|\bbelarus|\bbyelorus|\bbielorus"),
    ("Yiddish", r"\bидиш|на еврейском|\byiddish"),
    ("Hebrew", r"\bиврит|древнееврейск|\bhebrew"),
    ("Georgian", r"грузинск|\bgeorgian"),
    ("Armenian", r"армянск|\barmenian"),
    ("Polish", r"польск|\bpolish\b"),
    ("Romani", r"на цыганском|цыганском языке|\bromani\b|\bromanes\b|in romany|romany language"),
    ("Tatar", r"татарск|\btatar|\btartar"),
    ("Azerbaijani", r"азербайджанск|\bazerbaijan|\bazeri"),
    ("Kazakh", r"казахск|\bkazakh"),
    ("Uzbek", r"узбекск|\buzbek"),
    ("Bashkir", r"башкирск|\bbashkir"),
    ("Chuvash", r"чувашск|\bchuvash"),
    ("Latvian", r"латышск|\blatvian|\blettish"),
    ("Lithuanian", r"литовск|\blithuanian"),
    ("Estonian", r"эстонск|\bestonian"),
    ("Finnish", r"финск|\bfinnish"),
    ("German", r"немецк|\bgerman\b"),
    ("French", r"французск|\bfrench\b"),
    ("Italian", r"итальянск|\bitalian\b"),
    ("English", r"английск|\benglish\b"),
    ("Spanish", r"испанск|\bspanish\b"),
    ("Bulgarian", r"болгарск|\bbulgarian"),
    ("Serbian", r"сербск|\bserbian"),
    ("Czech", r"чешск|\bczech"),
    ("Romanian", r"румынск|молдавск|\bromanian|\bmoldavian|\bmoldovan"),
    ("Greek", r"греческ|\bgreek\b"),
    ("Hungarian", r"венгерск|\bhungarian"),
    ("Latin", r"латинск|на латыни|\blatin\b"),
    ("Church_Slavonic", r"церковнославянск|церковно-славянск|church slavonic|old church slavonic"),
]
WEAK_LANG_PATTERNS: list[tuple[str, str]] = [
    ("Romani", r"цыганск|\bgypsy|\bgipsy|\btzigane"),
    ("Yiddish", r"еврейск|\bjewish|\bklezmer|клезмер"),
]

# Abbreviations used in record catalogues, only honoured in parentheses or
# in the form "на укр. яз.".
_ABBREV = {
    "укр": "Ukrainian", "бел": "Belarusian", "белор": "Belarusian", "евр": "Yiddish",
    "груз": "Georgian", "арм": "Armenian", "польск": "Polish", "пол": "Polish",
    "цыг": "Romani", "тат": "Tatar", "нем": "German", "франц": "French", "фр": "French",
    "итал": "Italian", "англ": "English", "исп": "Spanish", "лат": "Latin",
    "болг": "Bulgarian", "чеш": "Czech", "латыш": "Latvian", "литов": "Lithuanian",
    "эст": "Estonian", "фин": "Finnish", "рум": "Romanian",
}
_ABBREV_RE = re.compile(
    r"\(\s*(?:на\s+)?(" + "|".join(sorted(_ABBREV, key=len, reverse=True)) + r")\.?(?:\s*яз\.?)?\s*\)"
    r"|\bна\s+(" + "|".join(sorted(_ABBREV, key=len, reverse=True)) + r")\.\s*яз",
    re.I)
_DESC_CONTEXT_RE = re.compile(
    r"на\s+([а-яё\-]+(?:ском|цком))\s+(?:языке|яз\.)"
    r"|(?:sung|performed|recorded|sings?)\s+in\s+([A-Za-z\-]+)"
    r"|\bin\s+([A-Z][a-z]+)\s+language"
    r"|\bязык(?:\s+исполнения)?\s*[:\-]\s*([а-яё\-]+)"
    r"|\blanguage\s*[:\-]\s*([A-Za-z\-]+)",
    re.I)

RUSSIAN_EXPLICIT_RE = re.compile(r"русск|\brussian\b", re.I)

# --- media type keywords ----------------------------------------------------
SPEECH_CATEGORY_RE = re.compile(
    r"радио|\bradio|речь|речи\b|speech|spoken|выступлени|доклад|лекци|lecture|интервью|"
    r"interview|обращени|репортаж|reportage|литературн|чтени|декламац|recitation|"
    r"сказк|fairy tale|радиоспектакл|спектакл|audio ?book|аудиокниг|новости|news|"
    r"хроник|chronicle|разговорн", re.I)
SPEECH_TITLE_RE = re.compile(
    r"\bречь\b|выступление|доклад|лекция|интервью|обращение\s+к|\bчитает\b|чтение|"
    r"декламаци|мелодекламаци|репортаж|радиоспектакль|\bspeech\b|\blecture\b|"
    r"\binterview\b|recitation|\breads\b|address to", re.I)
PRINT_RE = re.compile(
    r"газет|newspaper|журнал|magazine|periodical|периодик|пресс|\bpress\b|вырезк|"
    r"clipping|афиш|poster|плакат|каталог|catalogue|catalog\b|буклет|booklet|"
    r"листовк|leaflet|\bноты\b|sheet music|реклам|advertis|статья|article", re.I)

# --- instrumental ------------------------------------------------------------
INSTR_CATEGORY_RE = re.compile(
    r"инструментал|instrumental|оркестров|orchestral|\bмарш|\bmarch|духов|brass band|"
    r"военн\w* оркестр|military band|фортепиан|\bpiano|скрипк|violin|виолончел|cello|"
    r"балалайк|balalaika|гармон|баян|accordion|\bгитар\w* соло|guitar solo|симфони|"
    r"symphon|камерн\w* музык|chamber music|chamber piece|камерн\w* пьес|\bsolo\b|"
    r"\bсоло\b|solo piece|dance orchestra|танцевальн\w* оркестр|\bflute|флейт|cornet|"
    r"корнет|xylophon|ксилофон|\bharp\b|\bарф|zither|\bцитр|mandolin|мандолин|\borgan\b|"
    r"\bорган\b", re.I)
INSTR_TITLE_RE = re.compile(
    r"\bмарш\b|\bmarch\b|\bвальс\b|\bwaltz\b|\bvalse\b|\bполька\b|\bpolka\b|мазурк|"
    r"mazurka|\bгалоп\b|\bgalop\b|кадрил|quadrille|увертюр|overture|попурри|potpourri|"
    r"фантази|fantasi|вариаци|variations|\bэтюд|\betude|сонат|sonata|ноктюрн|nocturne|"
    r"симфони|symphon|концерт для|concerto|\bсюит|\bsuite\b|краковяк|лезгинк|гопак|"
    r"hopak|трепак|камаринск|полонез|polonaise|менуэт|minuet|гавот|gavotte|фокстрот|"
    r"foxtrot|fox-trot|\bтанго\b|\btango\b|тустеп|two-step|one-step|шимми|shimmy|"
    r"чарльстон|charleston|рэгтайм|регтайм|ragtime|\bсоло на\b|\bsolo\b|интермецц|"
    r"intermezzo|серенада для|\bпрелюди|prelude|\bрапсоди|rhapsody|скерцо|scherzo", re.I)
ENSEMBLE_RE = re.compile(
    r"оркестр|orchestra|\bband\b|ансамбль балалаечник|балалаечн|духовой|"
    r"струнн\w* квартет|string quartet|трио\b|trio\b|квинтет|quintet", re.I)
VOCAL_RE = re.compile(
    r"\bпо[её]т\b|\bпоют\b|исполняет песн|vocal|вокал|тенор|tenor|баритон|baritone|"
    r"\bбас\b|\bbass\b|сопрано|soprano|меццо|mezzo|контральто|contralto|\bальт\b|"
    r"\bхор\b|\bхора\b|\bхором\b|хоров|choir|chorus|\bsings?\b|\bsung\b|песня|песни|\bsong|"
    r"романс|romance|\bария\b|\baria\b|куплет|частушк|\bдуэт|\bduet|в сопр|"
    r"с оркестром|with orchestra|acc\. by|accompanied|певец|певица|singer|\bслова\b|"
    r"\bсл\.|lyrics|words by|\bfeat\b", re.I)


@dataclass
class LanguageResult:
    language: str
    method: str
    note: str

    @property
    def iso(self) -> str:
        return ISO639_2.get(self.language, "und")


@dataclass
class ItemContext:
    """Everything the classifier looks at, pre-flattened to strings."""
    title: str
    original_title: str
    category_path: list[str]
    description: str
    keywords: str
    language_field: str
    artist: str
    accompaniment: str
    genre: str
    lyricist: str
    has_audio: bool
    has_images: bool
    catalog_category: str = ""   # site's own "Catalog category" (Orchestra, Vocal, ...)
    is_document: bool = False    # a PDF scan is attached (book, periodical)


# ---------------------------------------------------------------------------
# Media type
# ---------------------------------------------------------------------------

def classify_media(ctx: ItemContext) -> str:
    cats = " / ".join(ctx.category_path + [ctx.genre, ctx.catalog_category])
    if ctx.has_audio:
        if SPEECH_CATEGORY_RE.search(cats) or SPEECH_TITLE_RE.search(ctx.title):
            # A song *about* the radio stays music unless the text says singing.
            if not (VOCAL_RE.search(ctx.title) and not SPEECH_CATEGORY_RE.search(cats)):
                return RADIO
        return MUSIC
    if ctx.has_images:
        if ctx.is_document or PRINT_RE.search(cats) or PRINT_RE.search(ctx.title):
            return PRINT
        return IMAGE
    return NONE


# ---------------------------------------------------------------------------
# Language
# ---------------------------------------------------------------------------

def _match_table(text: str, table: list[tuple[str, str]]) -> str | None:
    t = text.lower().replace("ё", "е")
    for lang, rx in table:
        if re.search(rx, t, re.I):
            return lang
    return None


def _lang_from_word(word: str) -> str | None:
    return _match_table(word, STRONG_LANG_PATTERNS) or (
        "Russian" if RUSSIAN_EXPLICIT_RE.search(word) else None)


def explicit_language(ctx: ItemContext) -> tuple[str | None, str, str | None, str]:
    """Return (strong_lang, note, weak_hint, russian_category_note).

    A category naming Russian is returned separately: on a site called
    russian-records a broad "Russian ..." category must not hide an
    instrumental or a decisively Ukrainian/Yiddish title."""
    if ctx.language_field:
        # "polish, american" / "russian, german": the first listed value wins,
        # not whichever happens to come first in the pattern table.
        for part in re.split(r"\s*(?:[,;/&+|]|\band\b|\bи\b)\s*", ctx.language_field):
            lang = _lang_from_word(part) if part else None
            if lang:
                return lang, f"language field '{ctx.language_field}'", None, ""
    russian_note = ""
    for name in reversed(ctx.category_path):  # leaf category first
        lang = _match_table(name, STRONG_LANG_PATTERNS)
        if lang:
            return lang, f"category '{name}'", None, ""
        if not russian_note and RUSSIAN_EXPLICIT_RE.search(name):
            russian_note = f"category '{name}'"
    for text in (ctx.title, ctx.original_title, ctx.genre):
        m = _ABBREV_RE.search(text or "")
        if m:
            abbr = (m.group(1) or m.group(2)).lower()
            return _ABBREV[abbr], f"abbreviation '{m.group(0).strip()}'", None, ""
    for text in (ctx.description, ctx.keywords):
        m = _ABBREV_RE.search(text or "")
        if m:
            abbr = (m.group(1) or m.group(2)).lower()
            return _ABBREV[abbr], f"abbreviation in notes '{m.group(0).strip()}'", None, ""
        for m in _DESC_CONTEXT_RE.finditer(text or ""):
            word = next(g for g in m.groups() if g)
            lang = _lang_from_word(word)
            if lang:
                return lang, f"notes phrase '{m.group(0).strip()}'", None, ""
    weak_src = " / ".join(ctx.category_path + [ctx.genre, ctx.keywords])
    return None, "", _match_table(weak_src, WEAK_LANG_PATTERNS), russian_note


def is_instrumental(ctx: ItemContext) -> tuple[bool, str]:
    every = " | ".join([ctx.title, ctx.original_title, ctx.description, ctx.keywords,
                        ctx.artist, ctx.accompaniment, ctx.genre, ctx.lyricist,
                        " / ".join(ctx.category_path)])
    vocal = VOCAL_RE.search(every)
    if vocal or ctx.lyricist.strip():
        return False, ""
    cats = " / ".join(ctx.category_path + [ctx.genre, ctx.catalog_category])
    m = INSTR_CATEGORY_RE.search(cats)
    if m:
        return True, f"instrumental category '{m.group(0)}', no vocal markers"
    m = INSTR_TITLE_RE.search(ctx.title) or INSTR_TITLE_RE.search(ctx.original_title)
    if m:
        return True, f"instrumental form '{m.group(0)}' in title, no vocal markers"
    m = ENSEMBLE_RE.search(ctx.artist)
    if m:
        return True, f"performer is an ensemble ('{m.group(0)}'), no vocal markers"
    return False, ""


# --- script analysis --------------------------------------------------------

def script_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ch in text:
        if not ch.isalpha():
            continue
        cp = ord(ch)
        if 0x0400 <= cp <= 0x052F or 0x1C80 <= cp <= 0x1C8F or 0xA640 <= cp <= 0xA69F:
            s = "cyrillic"
        elif 0x0590 <= cp <= 0x05FF or 0xFB1D <= cp <= 0xFB4F:
            s = "hebrew"
        elif 0x10A0 <= cp <= 0x10FF or 0x1C90 <= cp <= 0x1CBF:
            s = "georgian"
        elif 0x0530 <= cp <= 0x058F:
            s = "armenian"
        elif 0x0370 <= cp <= 0x03FF or 0x1F00 <= cp <= 0x1FFF:
            s = "greek"
        elif 0x0600 <= cp <= 0x06FF:
            s = "arabic"
        elif "LATIN" in unicodedata.name(ch, ""):
            s = "latin"
        else:
            s = "other"
        counts[s] = counts.get(s, 0) + 1
    return counts


_PRE_REFORM_FINAL_HARD = re.compile(r"[бвгджзклмнпрстфхцчшщ]ъ(?=[^а-яёіѣѳѵ]|$)")
_I_BEFORE_VOWEL = re.compile(r"і(?=[аеёиоуыэюяйѣ])")


def cyrillic_orthography(text: str) -> tuple[str | None, str]:
    """Decisive letter-level rules. Returns (language|None, note)."""
    t = text.lower()
    if "ѫ" in t or "ѭ" in t:
        return "Bulgarian", "pre-1945 Bulgarian letter ѫ"
    if any(c in t for c in "ѣѳѵ"):
        return "Russian", "pre-reform Russian orthography (ѣ/ѳ/ѵ)"
    if _PRE_REFORM_FINAL_HARD.search(t):
        return "Russian", "pre-reform Russian word-final ъ"
    if any(c in t for c in "ђћџ"):
        return "Serbian", "Serbian letters ђ/ћ/џ"
    if any(c in t for c in "ѓќѕ"):
        return "Macedonian", "Macedonian letters ѓ/ќ/ѕ"
    if any(c in t for c in "їєґ"):
        return "Ukrainian", "Ukrainian letters ї/є/ґ"
    if "ў" in t:
        return "Belarusian", "Belarusian letter ў"
    if any(c in t for c in "әғқңөұүһі") and any(c in t for c in "әғқңөұү"):
        return "Kazakh", "Kazakh letters"
    if "і" in t:
        if re.search(r"і[еи](?=[^а-яёі]|$)", t):
            return "Russian", "pre-reform Russian ending -іе/-іи"
        if "и" not in t and "щ" not in t and any(c in t for c in "ыэё"):
            return "Belarusian", "і with ы/э and no и"
        if "и" in t and all(_I_BEFORE_VOWEL.match(t, m.start()) for m in re.finditer("і", t)):
            return "Russian", "pre-reform і (only before vowels) alongside и"
        if not any(c in t for c in "ыэёъ"):
            return "Ukrainian", "і without Russian-only letters ы/э/ё/ъ"
    return None, ""


def _allowed_cyrillic(t: str) -> set[str]:
    allowed = {"Russian", "Ukrainian", "Belarusian", "Bulgarian", "Serbian", "Macedonian"}
    if any(c in t for c in "ыэёъ"):
        allowed.discard("Ukrainian")
    if any(c in t for c in "ищъ"):
        allowed.discard("Belarusian")
    if any(c in t for c in "ыэё"):
        allowed -= {"Bulgarian", "Serbian", "Macedonian"}
    if any(c in t for c in "щъйяюэы"):
        allowed -= {"Serbian", "Macedonian"}
    return allowed


@lru_cache(maxsize=None)
def _detector(names: tuple[str, ...]):
    from lingua import Language, LanguageDetectorBuilder
    langs = [getattr(Language, n.upper()) for n in names]
    return LanguageDetectorBuilder.from_languages(*langs).build()


def _lingua_top(text: str, names: set[str]) -> tuple[str, float, float]:
    """Return (best_language, confidence, confidence_of_russian)."""
    names = tuple(sorted(names))
    values = _detector(names).compute_language_confidence_values(text)
    if not values:
        return "", 0.0, 0.0
    by_name = {v.language.name.title(): v.value for v in values}
    best = values[0]
    return best.language.name.title(), best.value, by_name.get("Russian", 0.0)


LATIN_CANDIDATES = {"English", "French", "German", "Italian", "Polish", "Spanish", "Latin",
                    "Czech", "Latvian", "Lithuanian", "Estonian", "Finnish", "Romanian",
                    "Hungarian", "Portuguese", "Swedish", "Dutch", "Slovak", "Croatian",
                    "Turkish", "Azerbaijani", "Danish"}


def script_language(ctx: ItemContext, weak_hint: str | None) -> LanguageResult:
    texts = [t for t in (ctx.original_title, ctx.title) if t and t.strip()]
    text = " ".join(texts)
    counts = script_counts(text)
    for script, lang in (("hebrew", "Yiddish"), ("georgian", "Georgian"),
                         ("armenian", "Armenian"), ("greek", "Greek")):
        if counts.get(script, 0) >= 3:
            note = f"{script} script in title"
            if script == "hebrew":
                note += " (assumed Yiddish; Hebrew needs explicit metadata)"
            return LanguageResult(lang, SCRIPT_NLP, note)

    cyr_words = " ".join(re.findall(r"[^\W\d_]*[Ѐ-ԯ][^\W\d_]*", text))
    if counts.get("cyrillic", 0) >= 3:
        lang, note = cyrillic_orthography(cyr_words)
        if lang:
            return LanguageResult(lang, SCRIPT_NLP, note)
        t = cyr_words.lower()
        allowed = _allowed_cyrillic(t)
        letters = sum(1 for c in t if c.isalpha())
        if len(allowed) == 1:
            only = next(iter(allowed))
            return LanguageResult(only, SCRIPT_NLP, f"letter set rules out all but {only}")
        best, conf, ru = _lingua_top(cyr_words, allowed)
        if best != "Russian" and conf >= 0.85 and conf - ru >= 0.4 and letters >= 10:
            return LanguageResult(best, SCRIPT_NLP, f"lingua {best} {conf:.2f} (Russian {ru:.2f})")
        if weak_hint and ru < 0.9:
            return LanguageResult(weak_hint, EXPLICIT,
                                  f"weak category hint, text not confidently Russian ({ru:.2f})")
        if best == "Russian" and conf >= 0.6:
            return LanguageResult("Russian", SCRIPT_NLP, f"lingua Russian {conf:.2f}")
        return LanguageResult("Russian", DEFAULT_CYRILLIC,
                              f"Cyrillic, detector inconclusive ({best} {conf:.2f}); defaulted to Russian")

    if counts.get("latin", 0) >= 12:
        latin_text = " ".join(re.findall(r"[A-Za-zÀ-ÿĀ-žȘ-ț]+", text))
        best, conf, _ = _lingua_top(latin_text, LATIN_CANDIDATES)
        if conf >= 0.75:
            return LanguageResult(best, SCRIPT_NLP,
                                  f"lingua {best} {conf:.2f} on Latin-script title "
                                  "(may be a transliteration)")
        if weak_hint:
            return LanguageResult(weak_hint, EXPLICIT, "weak category hint, Latin title inconclusive")
        return LanguageResult(UNKNOWN_LANG, UNKNOWN, f"Latin-script title inconclusive ({best} {conf:.2f})")

    if weak_hint:
        return LanguageResult(weak_hint, EXPLICIT, "weak category hint only")
    return LanguageResult(UNKNOWN_LANG, UNKNOWN, "no usable text")


def site_says_instrumental(ctx: ItemContext) -> tuple[bool, str]:
    """The site's own Catalog category / Genre ("Violin with Piano", "Dance
    Orchestra", "Solo piece") with no vocal markers anywhere.

    Checked *before* the language field because russian-records.com's
    "Language(s) or Ethnics" is an ethnicity/origin tag: instrumental records
    carry values like "italian" or "polish, american"."""
    site = " / ".join(x for x in (ctx.catalog_category, ctx.genre) if x)
    if not site:
        return False, ""
    m = INSTR_CATEGORY_RE.search(site) or INSTR_TITLE_RE.search(site)
    if not m:
        return False, ""
    every = " | ".join([ctx.title, ctx.original_title, ctx.description, ctx.keywords,
                        ctx.artist, ctx.genre, ctx.catalog_category, ctx.lyricist])
    if VOCAL_RE.search(every) or ctx.lyricist.strip():
        return False, ""
    return True, f"site catalog category/genre '{site}', no vocal markers"


def classify_language(ctx: ItemContext, media_type: str) -> LanguageResult:
    if media_type == MUSIC:
        instr, why = site_says_instrumental(ctx)
        if instr:
            return LanguageResult(INSTRUMENTAL_LANG, INSTRUMENTAL, why)
    lang, note, weak, russian_note = explicit_language(ctx)
    if lang:
        if lang == "Russian":
            russian_note = russian_note or note
        else:
            return LanguageResult(lang, EXPLICIT, note)
    if media_type == MUSIC:
        instr, why = is_instrumental(ctx)
        if instr:
            return LanguageResult(INSTRUMENTAL_LANG, INSTRUMENTAL, why)
    result = script_language(ctx, None if russian_note else weak)
    if russian_note and (result.language in ("Russian", UNKNOWN_LANG)
                         or result.method in (DEFAULT_CYRILLIC, UNKNOWN)):
        return LanguageResult("Russian", EXPLICIT, russian_note)
    return result
