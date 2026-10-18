"""Build docs/index.html (GitHub Pages) from README.md.

The page is a styled shell - Soviet newspaper masthead, newsprint and carmine -
that renders the README in the browser with marked + mermaid, so the blog and
the README never drift apart. Repo-relative links are rewritten to GitHub URLs
because Pages serves docs/ as the site root.

    python tools/build_docs.py [--repo https://github.com/<user>/<repo>]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPO = "https://github.com/mduomarco-sketch/patefon-gramophone-archive"

TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Building Patefon</title>
<meta name="description" content="An engineering case study in digital preservation: archiving and streaming early 20th-century gramophone records with Python, FastAPI and adaptive congestion control.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@500;600;700&family=PT+Mono&family=PT+Serif:ital,wght@0,400;0,700;1,400&display=swap" rel="stylesheet">
<style>
:root {
  --paper: #F3EBDA; --paper-2: #EADFC8; --ink: #1C1612; --ink-2: #4A3F35; --ink-3: #7A6C5D;
  --red: #B3261E; --red-ink: #8E1B15; --brass: #A87B22; --rule: #1C1612;
  --code-bg: #E6D9BE; --mermaid-line: #1C1612;
  --font-poster: "Oswald", "Arial Narrow", sans-serif;
  --font-body: "PT Serif", Georgia, serif;
  --font-mono: "PT Mono", ui-monospace, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper: #14100F; --paper-2: #1C1513; --ink: #EFE4CF; --ink-2: #C9B9A2; --ink-3: #9A8A76;
    --red: #E0483D; --red-ink: #F06A5F; --brass: #E5B84C; --rule: #EFE4CF;
    --code-bg: #221A17; --mermaid-line: #EFE4CF;
  }
}
:root[data-theme="dark"] {
  --paper: #14100F; --paper-2: #1C1513; --ink: #EFE4CF; --ink-2: #C9B9A2; --ink-3: #9A8A76;
  --red: #E0483D; --red-ink: #F06A5F; --brass: #E5B84C; --rule: #EFE4CF;
  --code-bg: #221A17; --mermaid-line: #EFE4CF;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--paper); color: var(--ink); font: 18px/1.65 var(--font-body); }
.wrap { max-width: 780px; margin: 0 auto; padding: 0 16px 80px; }

/* masthead */
.mast { border-bottom: 4px double var(--rule); padding: 28px 0 14px; text-align: center; }
.mast-top { display: flex; justify-content: space-between; gap: 12px; font: 600 11px/1 var(--font-poster);
  letter-spacing: .22em; text-transform: uppercase; color: var(--ink-3); flex-wrap: wrap; }
.mast-name { font: 700 clamp(46px, 11vw, 92px)/.95 var(--font-poster); letter-spacing: .06em; color: var(--red);
  margin: 14px 0 6px; text-transform: uppercase; }
.mast-name .star { color: var(--red); font-size: .55em; vertical-align: .32em; margin: 0 .18em; }
.mast-sub { font: 600 13px/1.3 var(--font-poster); letter-spacing: .3em; text-transform: uppercase; color: var(--ink-2); }
.mast-strip { margin-top: 14px; border-top: 1px solid var(--rule); border-bottom: 1px solid var(--rule);
  padding: 6px 0; font: 600 11px/1.2 var(--font-poster); letter-spacing: .24em; text-transform: uppercase; }
.mast-strip a { color: var(--red); text-decoration: none; }

/* article */
article h1 { font: 700 clamp(28px, 5.4vw, 42px)/1.12 var(--font-poster); text-transform: uppercase; letter-spacing: .01em;
  margin: 34px 0 6px; }
article p.dek { font-size: 19px; font-style: italic; color: var(--ink-3); margin-top: 0; }
article h2 { font: 700 23px/1.2 var(--font-poster); text-transform: uppercase; letter-spacing: .04em; color: var(--red);
  margin: 52px 0 14px; padding-top: 12px; border-top: 3px solid var(--rule); }
article h3 { font: 600 18px/1.3 var(--font-poster); text-transform: uppercase; letter-spacing: .06em; margin: 32px 0 8px; }
article p.lede::first-letter {
  float: left; font: 700 3.3em/.82 var(--font-poster); color: var(--red); margin: .06em .1em 0 0; }
article p, article li { color: var(--ink); }
article a { color: var(--red-ink); text-decoration-thickness: 1px; text-underline-offset: 2px; }
article strong { color: var(--ink); }
article hr { border: 0; border-top: 1px solid var(--rule); margin: 36px 0; opacity: .5; }
article ul, article ol { padding-left: 1.3em; }
article li { margin: .3em 0; }
article code { font: 15px var(--font-mono); background: var(--code-bg); padding: .08em .3em; border-radius: 3px; }
article pre { background: var(--code-bg); border-left: 4px solid var(--red); padding: 14px 16px; overflow-x: auto;
  border-radius: 2px; font-size: 14px; line-height: 1.5; }
article pre code { background: none; padding: 0; font-size: inherit; }
article blockquote { margin: 22px 0; padding: 14px 18px; background: var(--paper-2); border-left: 4px solid var(--brass); }
article blockquote p { margin: .4em 0; }
article table { border-collapse: collapse; width: 100%; margin: 18px 0; font-size: 16px; display: block; overflow-x: auto; }
article th { font: 600 12px var(--font-poster); letter-spacing: .14em; text-transform: uppercase; text-align: left;
  border-bottom: 2px solid var(--rule); padding: 8px 10px; white-space: nowrap; }
article td { border-bottom: 1px solid color-mix(in srgb, var(--rule) 25%, transparent); padding: 7px 10px; vertical-align: top; }
article td:last-child, article th:last-child { text-align: right; }
article .mermaid { background: var(--paper-2); border: 1px solid color-mix(in srgb, var(--rule) 30%, transparent);
  padding: 16px; margin: 22px 0; overflow-x: auto; text-align: center; border-left: 4px solid var(--red); }
.foot { margin-top: 60px; border-top: 4px double var(--rule); padding-top: 14px; text-align: center;
  font: 600 11px/1.6 var(--font-poster); letter-spacing: .2em; text-transform: uppercase; color: var(--ink-3); }
.foot a { color: var(--red); }
.noscript { padding: 20px; background: var(--paper-2); }
@media (max-width: 600px) { body { font-size: 17px; } .mast-top span:nth-child(2) { display: none; } }
</style>
</head>
<body>
<div class="wrap">
  <header class="mast">
    <div class="mast-top"><span>Engineering case study</span><span>Digital preservation</span><span>Python · FastAPI · Web Audio</span></div>
    <div class="mast-name">Патефон<span class="star">★</span>Patefon</div>
    <div class="mast-sub">The gramophone archive</div>
    <div class="mast-strip"><a href="__REPO__">Source on GitHub</a> · 78 RPM · 34 languages · 1890s–1960s</div>
  </header>
  <article id="post"><noscript><div class="noscript">This page renders the project README with JavaScript. Read it on <a href="__REPO__">GitHub</a>.</div></noscript></article>
  <footer class="foot">Code only: no recordings or scans are redistributed · <a href="__REPO__">patefon-gramophone-archive</a></footer>
</div>
<script id="readme" type="application/json">__README__</script>
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>
<script>
(function () {
  var md = JSON.parse(document.getElementById("readme").textContent);
  var post = document.getElementById("post");
  post.innerHTML = marked.parse(md, { gfm: true });          // trusted: our own README
  // italic standfirst under the headline, then one drop cap on the first real paragraph
  var p = post.querySelector("h1") && post.querySelector("h1").nextElementSibling;
  if (p && p.tagName === "P" && p.children.length === 1 && p.firstElementChild.tagName === "EM"
      && p.textContent.trim() === p.firstElementChild.textContent.trim()) {
    p.classList.add("dek"); p = p.nextElementSibling;
  }
  while (p && p.tagName !== "P") p = p.nextElementSibling;
  if (p) p.classList.add("lede");
  // GitHub-style heading anchors so the table of contents works
  var seen = {};
  post.querySelectorAll("h1,h2,h3,h4").forEach(function (h) {
    var slug = h.textContent.trim().toLowerCase().replace(/[^\p{L}\p{N}\s_-]/gu, "").replace(/\s/g, "-");
    if (seen[slug] != null) { seen[slug] += 1; slug += "-" + seen[slug]; } else { seen[slug] = 0; }
    h.id = slug;
  });
  // ```mermaid blocks -> diagrams
  post.querySelectorAll("pre > code.language-mermaid").forEach(function (code) {
    var div = document.createElement("div");
    div.className = "mermaid";
    div.textContent = code.textContent;
    code.parentNode.replaceWith(div);
  });
  var css = getComputedStyle(document.documentElement);
  var v = function (n) { return css.getPropertyValue(n).trim(); };
  // deterministicIds: by default mermaid names each SVG "mermaid-<Date.now()>"; diagrams
  // rendered within the same millisecond then share an id and get drawn into the first one.
  mermaid.initialize({ startOnLoad: false, deterministicIds: true, theme: "base", securityLevel: "strict", themeVariables: {
    fontFamily: "PT Serif, Georgia, serif", fontSize: "14px",
    primaryColor: v("--paper"), primaryTextColor: v("--ink"), primaryBorderColor: v("--red"),
    lineColor: v("--mermaid-line"), secondaryColor: v("--paper-2"), tertiaryColor: v("--paper-2"),
    edgeLabelBackground: v("--paper-2"), clusterBkg: v("--paper-2")
  }});
  // measure node boxes with the real web font, or labels overflow their boxes
  (document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve())
    .then(function () { return mermaid.run({ querySelector: ".mermaid" }); });
  if (location.hash) { var t = document.getElementById(decodeURIComponent(location.hash.slice(1))); if (t) t.scrollIntoView(); }
})();
</script>
</body>
</html>
"""


def absolutize_links(md: str, repo: str) -> str:
    """](path) -> ](repo/blob/main/path) for repo-relative links (not #, http, mailto)."""
    return re.sub(r"\]\((?!https?://|#|mailto:)([^)\s]+)\)",
                  lambda m: f"]({repo}/blob/main/{m.group(1)})", md)


def build(repo: str = DEFAULT_REPO) -> Path:
    md = absolutize_links((ROOT / "README.md").read_text(encoding="utf-8"), repo)
    payload = json.dumps(md, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.replace("__README__", payload).replace("__REPO__", repo)
    out = ROOT / "docs" / "index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html, encoding="utf-8")
    (out.parent / ".nojekyll").write_text("", encoding="utf-8")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repo URL used for source links")
    print("wrote", build(ap.parse_args().repo))
