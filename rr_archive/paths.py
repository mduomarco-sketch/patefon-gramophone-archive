"""Filesystem-safe naming and archive path layout."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from . import config

_REPLACE = {"/": "-", "\\": "-", "|": "-", ":": " -", '"': "'", "*": "", "?": "", "<": "", ">": ""}
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def sanitize_segment(text: str | None, fallback: str, max_len: int = config.SEGMENT_MAX) -> str:
    s = unicodedata.normalize("NFC", text or "")
    s = _CONTROL_RE.sub(" ", s)
    for bad, good in _REPLACE.items():
        s = s.replace(bad, good)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip(". ").lstrip(" ")
    if len(s) > max_len:
        s = s[:max_len].rstrip(". ")
    if not s:
        s = fallback
    if s.split(".")[0].upper() in _RESERVED:
        s += "_"
    return s


def _shrink(parts: list[str], shrinkable: list[int], total_len, budget: int) -> list[str]:
    """Trim the longest shrinkable segment until the path fits."""
    parts = list(parts)
    while total_len(parts) > budget:
        idx = max(shrinkable, key=lambda i: len(parts[i]))
        if len(parts[idx]) <= 20:
            break
        parts[idx] = parts[idx][: max(20, len(parts[idx]) - 10)].rstrip(". ")
    return parts


def build_path(root: Path, folders: list[str], title: str, year: str, item_id: str, ext: str,
               budget: int = config.PATH_BUDGET) -> Path:
    """{root}/{folders...}/{year} - {title} [{item_id}]{ext}, kept under budget.

    The [item_id] suffix is never trimmed, so names stay unique."""
    year = sanitize_segment(year, "Unknown_Year", 20)
    title = sanitize_segment(title, "Untitled")
    suffix = f" [{sanitize_segment(item_id, 'noid', 40)}]{ext}"
    parts = folders + [title]
    root_len = len(str(root.resolve())) + 1

    def total(ps):
        return root_len + sum(len(p) + 1 for p in ps[:-1]) + len(f"{year} - {ps[-1]}{suffix}")

    # Fixed top-level branch/language folders are not shrunk.
    shrinkable = [i for i in range(len(parts)) if i >= len(folders) - 2]
    parts = _shrink(parts, shrinkable, total, budget)
    return root.joinpath(*parts[:-1], f"{year} - {parts[-1]}{suffix}")


def sidecar_path(main: Path, label: str, index: int, ext: str) -> Path:
    """Label scan saved next to its audio file: 'stem (label).jpg', 'stem (label 2).jpg'."""
    tag = label if index == 0 else f"{label} {index + 1}"
    return main.with_name(f"{main.stem} ({tag}){ext}")
