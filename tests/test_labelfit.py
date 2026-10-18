"""Spindle-hole detection for off-centre label scans."""

import json

import pytest

pytest.importorskip("numpy")
PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw

import rr_archive.labelfit as lf
from rr_archive.export import export_catalog
from rr_archive.state import State


def record_scan(path, cx, cy, size=600, bg=(235, 235, 230)):
    """A synthetic shellac record: grooves, a label with text-like marks and a
    spindle hole, centred at (cx, cy) as fractions of the photo."""
    im = Image.new("RGB", (size, size), bg)
    d = ImageDraw.Draw(im)
    x, y = cx * size, cy * size
    for r in range(int(size * 0.47), int(size * 0.18), -6):        # grooves
        d.ellipse([x - r, y - r, x + r, y + r], fill=(18, 16, 14) if (r // 6) % 2 else (34, 30, 27))
    r = size * 0.17
    d.ellipse([x - r, y - r, x + r, y + r], fill=(196, 72, 52))       # label
    for k in range(9):                                                # "text"
        d.rectangle([x - r * 0.7 + k * 18, y + r * 0.35, x - r * 0.7 + k * 18 + 10, y + r * 0.45], fill=(240, 220, 180))
    r = size * 0.015
    d.ellipse([x - r, y - r, x + r, y + r], fill=bg)                 # hole
    im.save(path)


@pytest.mark.parametrize("cx,cy", [(0.5, 0.5), (0.42, 0.55), (0.58, 0.44), (0.47, 0.6)])
def test_detects_spindle_centre(tmp_path, cx, cy):
    p = tmp_path / "scan.jpg"
    record_scan(p, cx, cy)
    fx, fy, s = lf.compute_fit(p)
    assert abs(fx - cx) < 0.02 and abs(fy - cy) < 0.02, (fx, fy)
    off = max(abs(cx - 0.5), abs(cy - 0.5))
    assert s == pytest.approx(1.0 if off < 0.006 else 1 + 2 * max(abs(fx - 0.5), abs(fy - 0.5)), abs=1e-3)


def test_featureless_scan_stays_centred(tmp_path):
    p = tmp_path / "blank.jpg"
    Image.new("RGB", (300, 300), (200, 190, 170)).save(p)
    assert lf.compute_fit(p) == [0.5, 0.5, 1.0]


def test_update_is_incremental_and_export_embeds(built_archive, tmp_path, monkeypatch):
    db, root = built_archive
    fit_file = tmp_path / "label_fit.json"
    monkeypatch.setattr(lf, "FIT_PATH", fit_file)
    monkeypatch.setattr(lf.update_fits, "__defaults__", (fit_file, None))
    monkeypatch.setattr(lf.load_fits, "__defaults__", (fit_file,))
    state = State(db)
    labels = [json.loads(r["label_local_paths"])[0] for r in state.conn.execute(
        "SELECT label_local_paths FROM tracks WHERE status='COMPLETED' AND label_local_paths != '[]' "
        "AND media_type IN ('MUSIC','RADIO_SPEECH')")]
    for i, p in enumerate(labels):            # replace the tiny test JPEGs with off-centre scans
        record_scan(p, 0.44 + 0.02 * i, 0.53)
    first = lf.update_fits(state.conn)
    assert first["measured"] == len(labels) and lf.update_fits(state.conn)["measured"] == 0
    out = export_catalog(state, root)
    state.close()
    html = out.read_text(encoding="utf-8")
    data = json.loads(html.split('<script id="rr-data" type="application/json">', 1)[1].split("</script>", 1)[0])
    fitted = [i for i in data["items"] if i.get("fit")]
    assert fitted and all(abs(i["fit"][1] - 0.53) < 0.02 for i in fitted)
