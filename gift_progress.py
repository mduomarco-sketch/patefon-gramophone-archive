"""Live progress page for the gift build. Stdlib only; reads the source and
gift databases READ-ONLY (so it never disturbs the running builder) and serves
a small auto-refreshing page at http://127.0.0.1:8770 .

    python gift_progress.py
"""

import functools
import http.server
import json
import socketserver
import sqlite3
import webbrowser
from pathlib import Path

DESKTOP = Path.home() / "Desktop"
SOURCE_DB = DESKTOP / "russian_records" / "archive_state.db"
GIFT_DB = DESKTOP / "patefon_gift" / "archive_state.db"
PORT = 8770


def _ro(path):
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)


def progress():
    out = {"done": 0, "target": 0, "gift_bytes": 0, "source_bytes": 0, "ok": True}
    try:
        with _ro(SOURCE_DB) as s:
            row = s.execute("SELECT COUNT(*), COALESCE(SUM(file_size),0) FROM tracks WHERE status='COMPLETED'").fetchone()
            out["target"], out["source_bytes"] = row[0], row[1]
    except Exception as e:
        out["ok"] = False; out["error"] = f"source: {e}"
    try:
        if GIFT_DB.exists():
            with _ro(GIFT_DB) as g:
                row = g.execute("SELECT COUNT(*), COALESCE(SUM(file_size),0) FROM tracks WHERE status='COMPLETED'").fetchone()
                out["done"], out["gift_bytes"] = row[0], row[1]
    except Exception as e:
        out["ok"] = False; out["error"] = f"gift: {e}"
    return out


PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Gift build progress</title>
<style>
:root{color-scheme:dark}
body{margin:0;background:radial-gradient(1000px 500px at 70% -10%,rgba(204,41,41,.14),transparent),#0F0E0E;
 color:#F0E6D2;font:16px/1.5 "Segoe UI",system-ui,sans-serif;display:grid;place-items:center;min-height:100vh}
.card{width:min(560px,92vw);background:#1A1211;border:1px solid rgba(229,184,76,.2);border-radius:14px;
 padding:30px 32px;box-shadow:0 30px 70px rgba(0,0,0,.5)}
h1{font:600 22px/1.2 "Cormorant Garamond",Georgia,serif;letter-spacing:.02em;margin:0 0 4px}
.sub{color:#C2B29F;font-size:13px;margin-bottom:22px}
.bar{height:16px;border-radius:999px;background:#241A18;border:1px solid rgba(229,184,76,.25);overflow:hidden}
.fill{height:100%;width:0;background:linear-gradient(90deg,#E5B84C,#CC2929);transition:width .6s ease}
.pct{text-align:center;font:700 34px/1.1 "Cascadia Mono",monospace;margin:18px 0 6px;color:#E5B84C;font-variant-numeric:tabular-nums}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:22px}
.stat{background:#241A18;border:1px solid rgba(229,184,76,.16);border-radius:10px;padding:12px 14px}
.stat b{display:block;font:700 20px "Cascadia Mono",monospace;font-variant-numeric:tabular-nums}
.stat span{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:#8E7F6E}
.done{margin-top:20px;text-align:center;color:#9FD8B0;font-weight:600;display:none}
.err{color:#F0A496;font-size:13px;margin-top:10px}
</style></head><body>
<div class=card>
 <h1>Patefon · building the gift</h1>
 <div class=sub>Reading the archive read-only · compacting into Desktop\\patefon_gift</div>
 <div class=bar><div class=fill id=fill></div></div>
 <div class=pct id=pct>—</div>
 <div class=grid>
  <div class=stat><b id=done>—</b><span>items compacted</span></div>
  <div class=stat><b id=target>—</b><span>items total</span></div>
  <div class=stat><b id=giftsize>—</b><span>gift size</span></div>
  <div class=stat><b id=saved>—</b><span>smaller than source</span></div>
  <div class=stat><b id=rate>—</b><span>items / min · avg</span></div>
  <div class=stat><b id=eta>—</b><span>est. time left</span></div>
 </div>
 <div class=done id=finished>✓ Gift build complete</div>
 <div class=err id=err></div>
</div>
<script>
let samples=[];
const WINDOW=120000; // rolling 2-minute average, so the rate/ETA don't jump poll-to-poll
function gb(n){return (n/1e9).toFixed(2)+" GB"}
function fmtEta(min){if(!isFinite(min)||min<=0)return "—";const h=Math.floor(min/60),m=Math.round(min%60);return h?`${h}h ${m}m`:`${m}m`}
async function tick(){
 let p;try{p=await (await fetch("/api/progress",{cache:"no-store"})).json()}catch(e){return}
 const done=p.done,target=p.target||0;
 const pct=target?Math.min(100,done/target*100):0;
 document.getElementById("fill").style.width=pct+"%";
 document.getElementById("pct").textContent=pct.toFixed(1)+"%";
 document.getElementById("done").textContent=done.toLocaleString();
 document.getElementById("target").textContent=target.toLocaleString();
 document.getElementById("giftsize").textContent=gb(p.gift_bytes);
 // savings estimate: gift size vs the proportional source size of what's done
 let saved="—";
 if(done&&p.source_bytes&&target){const projSrc=p.source_bytes*done/target; if(projSrc>0)saved=(100*(1-p.gift_bytes/projSrc)).toFixed(1)+"%";}
 document.getElementById("saved").textContent=saved;
 const now=Date.now();
 samples.push({t:now,done});
 while(samples.length>2 && now-samples[0].t>WINDOW) samples.shift();
 const first=samples[0], span=(now-first.t)/60000;   // minutes across the window
 if(span>=0.25 && done>first.done){                  // wait ~15s so the average is meaningful
  const r=(done-first.done)/span;
  document.getElementById("rate").textContent=r.toFixed(1);
  document.getElementById("eta").textContent=fmtEta((target-done)/r);
 }
 document.getElementById("err").textContent=p.ok?"":(p.error||"");
 if(target&&done>=target){document.getElementById("finished").style.display="block";
  document.getElementById("rate").textContent="0";document.getElementById("eta").textContent="done";}
}
tick();setInterval(tick,2000);
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/api/progress"):
            body = json.dumps(progress()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
        else:
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print(f"Gift progress: http://127.0.0.1:{PORT}")
    try:
        webbrowser.open(f"http://127.0.0.1:{PORT}/")
    except Exception:
        pass
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", PORT), Handler) as httpd:
        httpd.serve_forever()
