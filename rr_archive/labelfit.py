"""Find the spindle hole in label scans so the UI rotates each record around
its real centre instead of the photo's centre.

Scans are hand-made photos: the record is rarely exactly in the middle, so
spinning the image around its own centre makes the label wobble against the
painted spindle. For every label we store [cx, cy, scale]:
  cx, cy  hole centre as a fraction of width/height (0.5, 0.5 = centred)
  scale   zoom needed so the shifted photo still covers the whole disc

Results live in rr_archive/web/static/label_fit.json ({item_id: [cx, cy, s]}),
served to the dashboard as /static/label_fit.json and embedded into the gift.
Computed incrementally: only labels not yet measured are opened.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import config

FIT_PATH = Path(__file__).resolve().parent / "web" / "static" / "label_fit.json"
THUMB = 256
MIN_OFFSET = 0.006     # below this the photo is already centred
MAX_SCALE = 1.45


def symmetry_centre(gray, window: float = 0.25) -> tuple[float, float, float] | None:
    """Radial-symmetry vote. A record is concentric circles around its spindle
    (grooves, label rim, hole rim); every strong edge votes along its gradient
    line, so the true centre collects votes from all circles while text and
    artwork edges scatter. Returns (cx, cy, confidence) as fractions."""
    import numpy as np

    g = gray.astype(np.float32)
    h, w = g.shape
    gx = np.zeros_like(g)
    gy = np.zeros_like(g)
    gx[1:-1, 1:-1] = (g[:-2, 2:] + 2 * g[1:-1, 2:] + g[2:, 2:]) - (g[:-2, :-2] + 2 * g[1:-1, :-2] + g[2:, :-2])
    gy[1:-1, 1:-1] = (g[2:, :-2] + 2 * g[2:, 1:-1] + g[2:, 2:]) - (g[:-2, :-2] + 2 * g[:-2, 1:-1] + g[:-2, 2:])
    mag = np.hypot(gx, gy)
    thr = np.percentile(mag, 88)
    ys, xs = np.nonzero(mag > max(thr, 1e-3))
    if len(xs) < 50:
        return None
    if len(xs) > 9000:  # subsample for speed; voting is statistical anyway
        keep = np.random.default_rng(0).choice(len(xs), 9000, replace=False)
        ys, xs = ys[keep], xs[keep]
    ux = gx[ys, xs] / mag[ys, xs]
    uy = gy[ys, xs] / mag[ys, xs]
    side = min(w, h)
    t = np.concatenate([np.arange(-side * 0.55, -3), np.arange(4, side * 0.55)]).astype(np.float32)
    px = (xs[:, None] + ux[:, None] * t[None, :]).ravel()
    py = (ys[:, None] + uy[:, None] * t[None, :]).ravel()
    x0, x1, y0, y1 = w * window, w * (1 - window), h * window, h * (1 - window)
    ok = (px >= x0) & (px < x1) & (py >= y0) & (py < y1)
    acc = np.zeros((h, w), dtype=np.float32)
    np.add.at(acc, (py[ok].astype(np.int32), px[ok].astype(np.int32)), 1.0)
    # light blur so the peak is a stable region, not a single pixel
    k = np.ones(5, dtype=np.float32) / 5
    acc = np.apply_along_axis(lambda r: np.convolve(r, k, mode="same"), 1, acc)
    acc = np.apply_along_axis(lambda c: np.convolve(c, k, mode="same"), 0, acc)
    iy, ix = np.unravel_index(int(np.argmax(acc)), acc.shape)
    inner = acc[int(y0):int(y1), int(x0):int(x1)]
    conf = float(acc[iy, ix] / (inner.mean() + 1e-6))
    return (ix + 0.5) / w, (iy + 0.5) / h, conf


def compute_fit(path: Path) -> list[float]:
    import numpy as np
    from PIL import Image

    with Image.open(path) as im:
        im.draft("L", (THUMB * 2, THUMB * 2))   # JPEG: decode at 1/2-1/8 scale, several x faster
        im = im.convert("L")
        im.thumbnail((THUMB, THUMB))
        gray = np.asarray(im)
    h, w = gray.shape
    found = symmetry_centre(gray)
    if found is None or found[2] < 4.0:   # no clear concentric structure: leave centred
        return [0.5, 0.5, 1.0]
    cx, cy, _conf = found
    # object-fit: cover shows the central square of non-square photos, so
    # express the hole relative to that visible square.
    side = min(w, h)
    vx = (cx * w - (w - side) / 2) / side
    vy = (cy * h - (h - side) / 2) / side
    off = max(abs(vx - 0.5), abs(vy - 0.5))
    if off < MIN_OFFSET or not (0.2 < vx < 0.8 and 0.2 < vy < 0.8):
        return [0.5, 0.5, 1.0]
    return [round(float(vx), 4), round(float(vy), 4), round(float(min(MAX_SCALE, 1 + 2 * off)), 4)]


def load_fits(path: Path = FIT_PATH) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def update_fits(conn, path: Path = FIT_PATH, limit: int | None = None, *,
                workers: int | None = None, checkpoint: int = 250) -> dict:
    """Measure labels of COMPLETED audio items that have no fit yet.
    Uses only reads on conn (safe with a read-only connection).

    Runs on a thread pool (Pillow decoding and the numpy voting release the
    GIL) and saves every `checkpoint` measurements, so an interrupted export
    keeps its progress instead of starting over."""
    fits = load_fits(path)
    rows = conn.execute(
        "SELECT item_id, label_local_paths FROM tracks WHERE status='COMPLETED' "
        "AND media_type IN ('MUSIC','RADIO_SPEECH') AND label_local_paths IS NOT NULL "
        "AND label_local_paths != '[]'").fetchall()
    todo = [(item_id, labels) for item_id, labels in rows if item_id not in fits]
    if limit:
        todo = todo[:limit]

    def measure(row):
        item_id, labels = row
        try:
            return item_id, compute_fit(Path(json.loads(labels)[0]))
        except Exception:
            return item_id, [0.5, 0.5, 1.0]

    def save():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.part")
        tmp.write_text(json.dumps(fits, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)

    done = 0
    if todo:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers or min(8, os.cpu_count() or 2)) as pool:
            for item_id, fit in pool.map(measure, todo):    # results arrive in this thread
                fits[item_id] = fit
                done += 1
                if done % checkpoint == 0:
                    save()
        save()
    return {"measured": done, "total": len(fits),
            "recentred": sum(1 for v in fits.values() if v != [0.5, 0.5, 1.0])}


def _cli() -> None:
    import sqlite3
    conn = sqlite3.connect(f"file:{config.DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)
    print(update_fits(conn))
    conn.close()


if __name__ == "__main__":
    _cli()
