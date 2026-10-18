/* Dashboard-only admin layer on top of the Patefon app (catalog.js).
 * Uses only the existing API: /api/session, /api/job*, /api/recon,
 * /api/catalog, /api/items/*, /api/options, /api/export, /media/*. */
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const el = (tag, attrs, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
      else if (k === "class") n.className = v;
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const k of kids.flat()) if (k != null && k !== false) n.append(k instanceof Node ? k : document.createTextNode(String(k)));
    return n;
  };

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      ...opts,
      headers: { "Content-Type": "application/json", "X-RR-Dashboard": "1", ...(opts.headers || {}) },
    });
    const body = res.headers.get("content-type")?.includes("json") ? await res.json() : await res.text();
    if (!res.ok) throw new Error(body?.detail || body || res.statusText);
    return body;
  }
  const post = (path, data) => api(path, { method: "POST", body: JSON.stringify(data || {}) });

  // --- the music app ----------------------------------------------------------------
  const app = window.RRApp.mount(document.getElementById("patefon"), {
    items: () => api("/api/catalog"),
    detail: (id) => api(`/api/items/${encodeURIComponent(id)}`),
    inspect: (id) => api(`/api/items/${encodeURIComponent(id)}/inspect`),
    // spindle-hole offsets for label scans (python -m rr_archive.labelfit)
    fits: () => fetch(`/static/label_fit.json?t=${Date.now()}`).then((r) => (r.ok ? r.json() : {})),
    // lyrics already stored (public-domain Wikisource texts or files you added)
    lyrics: () => fetch(`/static/lyrics_index.json?t=${Date.now()}`).then((r) => (r.ok ? r.json() : {})),
    // the personal note shown on first open; edit rr_archive/web/static/dedication.json
    dedication: () => fetch(`/static/dedication.json?t=${Date.now()}`)
      .then((r) => (r.ok ? r.json() : fetch("/static/dedication.example.json").then((e) => (e.ok ? e.json() : null)))),
    // the archive's own label text / liner notes (python -m rr_archive.notes)
    notes: () => fetch(`/static/notes_index.json?t=${Date.now()}`).then((r) => (r.ok ? r.json() : {})),
    mediaUrl: (d) => `/media/${encodeURIComponent(d.id)}`,
    labelUrl: (d, i) => `/media/${encodeURIComponent(d.id)}/label/${i}`,
    pdfUrl: (d) => `/media/${encodeURIComponent(d.id)}/pdf`,
  });

  // --- gear, live dot, "new records" chip in the top bar ------------------------------
  const drawer = $("#admin-drawer"), scrim = $("#admin-scrim");
  const newChip = el("button", { class: "adm-new", title: "Add newly downloaded records to the library" });
  const gear = el("button", { class: "pf-icon-btn adm-gear", "aria-label": "Crawler & admin (Ctrl+Shift+A)",
    title: "Crawler & admin  (Ctrl+Shift+A)" }, "⚙", el("span", { class: "dot", "aria-hidden": "true" }));
  app.slot.append(newChip, gear);

  function openAdmin() {
    drawer.classList.add("open"); drawer.setAttribute("aria-hidden", "false"); scrim.classList.add("open");
    loadSession(); renderReclassify();
  }
  function closeAdmin() { drawer.classList.remove("open"); drawer.setAttribute("aria-hidden", "true"); scrim.classList.remove("open"); }
  gear.addEventListener("click", () => (drawer.classList.contains("open") ? closeAdmin() : openAdmin()));
  scrim.addEventListener("click", closeAdmin);
  $("#admin-close").addEventListener("click", closeAdmin);
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.shiftKey && (e.key === "A" || e.key === "a")) {
      e.preventDefault(); drawer.classList.contains("open") ? closeAdmin() : openAdmin();
    } else if (e.key === "Escape" && drawer.classList.contains("open")) closeAdmin();
  });

  let knownCount = null;
  async function refreshLibrary() {
    const added = await app.refresh();
    knownCount = app.count();
    newChip.classList.remove("show");
    $("#refresh-status").textContent = added > 0 ? `${added} new records added to the library.` : "Library is up to date.";
    if (added > 0) app.toast(`+${added} records`);
  }
  newChip.addEventListener("click", refreshLibrary);
  $("#btn-refresh-library").addEventListener("click", refreshLibrary);
  app.ready.then(() => { knownCount = app.count(); });

  // --- session -------------------------------------------------------------------------
  async function loadSession() {
    try {
      const s = await api("/api/session");
      $("#cookie-name").textContent = s.cookie_name_hint;
      $("#session-status").textContent = s.exists
        ? "Saved: " + s.cookies.map((c) => `${c.name}=${c.masked}`).join(", ") : "No cookies saved yet.";
      if (s.user_agent) $("#session-form").user_agent.value = s.user_agent;
    } catch (err) { $("#session-status").textContent = "Error: " + err.message; }
  }
  $("#session-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      await post("/api/session", { sessionid: f.sessionid.value, userid: f.userid.value,
        cookie_header: f.cookie_header.value, user_agent: f.user_agent.value });
      f.sessionid.value = f.userid.value = f.cookie_header.value = "";
      await loadSession();
    } catch (err) { $("#session-status").textContent = "Error: " + err.message; }
  });

  // --- jobs ------------------------------------------------------------------------------
  let lastMode = "test", shownReportFor = null;
  const QUEUE_KEY = "rr-queue-options";
  function startBody(mode) {
    const audioOnly = $("#audio-only").checked, priority = $("#audio-priority").checked;
    const limit = Math.max(0, parseInt($("#batch-limit").value, 10) || 0);
    const workers = Math.min(32, Math.max(1, parseInt($("#workers").value, 10) || 1));
    return { mode, retry_failed: $("#retry-failed").checked,
      queue: audioOnly ? "audio-only" : priority ? "audio-first" : "all", limit: mode === "crawl" ? limit : 0,
      workers: mode === "crawl" ? workers : 1 };
  }
  function syncQueueControls() {
    const p = $("#audio-priority");
    if ($("#audio-only").checked) { p.checked = true; p.disabled = true; } else p.disabled = false;
    try {
      localStorage.setItem(QUEUE_KEY, JSON.stringify({ priority: p.checked, audioOnly: $("#audio-only").checked,
        limit: $("#batch-limit").value, workers: $("#workers").value }));
    } catch { /* not remembered */ }
  }
  try {
    const saved = JSON.parse(localStorage.getItem(QUEUE_KEY) || "null");
    if (saved) {
      $("#audio-priority").checked = saved.priority !== false;
      $("#audio-only").checked = !!saved.audioOnly;
      $("#batch-limit").value = saved.limit || "0";
      if (saved.workers) $("#workers").value = saved.workers;
    }
  } catch { /* ignore */ }
  ["#audio-priority", "#audio-only", "#batch-limit", "#workers"].forEach((s) => $(s).addEventListener("change", syncQueueControls));
  const showWorkers = () => { $("#workers-out").textContent = $("#workers").value; };
  $("#workers").addEventListener("input", showWorkers);
  showWorkers();
  syncQueueControls();

  document.querySelectorAll("#admin-drawer [data-mode]").forEach((b) => b.addEventListener("click", async () => {
    if (b.dataset.mode === "crawl" && !confirm("Start the full archive crawl? It runs politely in the background and can be paused or stopped at any time.")) return;
    try { lastMode = b.dataset.mode; await post("/api/job/start", startBody(b.dataset.mode)); shownReportFor = null; poll(); }
    catch (err) { alert(err.message); }
  }));
  document.querySelectorAll("#admin-drawer [data-act]").forEach((b) => b.addEventListener("click", async () => {
    if (b.dataset.act === "kill" && !confirm("Force-kill the job? The current .part file is discarded and redone next run.")) return;
    try { await post(`/api/job/${b.dataset.act}`); poll(); } catch (err) { alert(err.message); }
  }));
  $("#btn-export").addEventListener("click", async () => {
    const lib = app.library();
    $("#export-status").textContent = "Exporting… (after a big crawl the first export measures new label scans and can take a few minutes; later ones take seconds)";
    try {
      const r = await post("/api/export", lib);
      if (r.seed_supported) {
        $("#export-status").textContent = `Written: ${r.path} - with ${r.playlists_embedded} playlists and ${r.liked_embedded} liked songs baked in.`;
      } else {
        // A dashboard started before playlist baking existed ignores them; it
        // picks this up after its next restart (start_dashboard.bat).
        $("#export-status").textContent = `Written: ${r.path} - without your ${lib.playlists.length} playlists and ${lib.liked.length} likes: `
          + "restart the dashboard once (after stopping the crawl) to bake them in, or use Back up library + --export-html --playlists.";
      }
    } catch (err) { $("#export-status").textContent = "Error: " + err.message; }
  });
  $("#btn-build-gift").addEventListener("click", async () => {
    $("#gift-status").textContent = "Syncing new tracks into the gift folder (background)…";
    try {
      const r = await post("/api/build-gift");
      $("#gift-status").textContent = `Started → ${r.dest} (4 workers, read-only source). Progress in ${r.log}`;
    } catch (err) { $("#gift-status").textContent = "Error: " + err.message; }
  });
  $("#banner-save").addEventListener("click", async () => {
    const sid = $("#banner-sid").value.trim();
    if (!sid) return;
    try {
      await post("/api/session", { sessionid: sid });
      $("#banner-sid").value = "";
      await loadSession();
      await post("/api/job/start", startBody(lastMode));
      poll();
    } catch (err) { alert(err.message); }
  });

  const stat = (label, value) => el("div", { class: "adm-stat" }, el("b", {}, value ?? 0), el("span", {}, label));
  // Live speedometer: turn successive /api/job readings into rates over a
  // rolling ~90 s window. Nothing is stored server-side.
  const speedo = [];
  function fmtEta(sec) {
    if (!isFinite(sec) || sec <= 0) return "—";
    const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  }
  function renderSpeedo(p, running) {
    const box = $("#speedo");
    if (!running) { box.hidden = true; speedo.length = 0; return; }
    box.hidden = false;
    const now = Date.now();
    speedo.push({ t: now, done: p.completed_total || 0, pages: p.pages_done || 0, bytes: p.bytes_total || 0 });
    while (speedo.length > 2 && now - speedo[0].t > 90000) speedo.shift();
    const a = speedo[0], b = speedo[speedo.length - 1], dt = (b.t - a.t) / 1000;
    if (dt < 5) return;                       // need a couple of readings first
    const tpm = (b.done - a.done) / dt * 60;
    const ppm = (b.pages - a.pages) / dt * 60;
    const mbps = (b.bytes - a.bytes) / dt / 1e6;
    $("#sp-tracks").textContent = tpm.toFixed(tpm < 10 ? 1 : 0);
    $("#sp-pages").textContent = ppm.toFixed(ppm < 10 ? 1 : 0);
    $("#sp-mbps").textContent = mbps >= 0 ? mbps.toFixed(2) : "—";
    const pendingAudio = (p.items || {}).PENDING || 0;
    $("#sp-eta").textContent = tpm > 0 ? fmtEta(pendingAudio / (tpm / 60)) : "—";
  }

  function renderStatus(data) {
    const j = data.job, p = data.progress;
    $("#job-pill").className = "adm-pill " + (j ? j.state : "");
    $("#job-pill").textContent = j ? `${j.mode}: ${j.state}` : "idle";
    const running = !!(j && j.running);
    const attention = !!(j && (j.state === "captcha" || j.state === "session_error"));
    gear.classList.toggle("busy", running && !attention);
    gear.classList.toggle("alert", attention);
    gear.title = attention ? "Crawler needs attention - session expired (Ctrl+Shift+A)"
      : running ? `Crawler ${j.state} (Ctrl+Shift+A)` : "Crawler & admin  (Ctrl+Shift+A)";
    ["#btn-recon", "#btn-test", "#btn-crawl"].forEach((s) => { $(s).disabled = running; });
    $("#btn-pause").disabled = !running || j.state === "paused";
    $("#btn-resume").disabled = !running || j.state !== "paused";
    $("#btn-stop").disabled = $("#btn-kill").disabled = !running;
    $("#captcha-banner").hidden = !attention;
    if (j) lastMode = j.mode;

    const items = p.items || {}, pages = p.pages || {};
    $("#stats").replaceChildren(stat("completed", items.COMPLETED), stat("pending", items.PENDING),
      stat("failed", items.FAILED), stat("skipped", items.SKIPPED), stat("pages done", pages.DONE), stat("pages queued", pages.PENDING));
    renderSpeedo(p, running);

    const log = $("#log");
    const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
    log.textContent = j ? j.log.join("\n") : "No job has run in this dashboard session yet.";
    if (atBottom) log.scrollTop = log.scrollHeight;

    // New downloads: offer to add them without touching playback.
    if (knownCount != null && typeof items.COMPLETED === "number") {
      const total = items.COMPLETED;
      if (!renderStatus.lastCompleted) renderStatus.lastCompleted = total;
      const fresh = total - renderStatus.lastCompleted;
      if (fresh > 0) { newChip.textContent = `+${fresh} new · Refresh`; newChip.classList.add("show"); }
    }
    if (j && !j.running && j.ended_at && shownReportFor !== j.started_at) {
      shownReportFor = j.started_at;
      if (j.mode === "recon") loadRecon();
      if (j.mode === "test" || j.mode === "crawl") loadJobItems();
    }
  }
  newChip.addEventListener("click", () => { renderStatus.lastCompleted = null; });
  $("#btn-refresh-library").addEventListener("click", () => { renderStatus.lastCompleted = null; });

  async function poll() { try { renderStatus(await api("/api/job")); } catch { /* server restarting */ } }
  setInterval(poll, 2500);

  // --- recon report ---------------------------------------------------------------------------
  const kvTable = (obj) => el("table", { class: "kv" }, Object.entries(obj).map(([k, v]) =>
    el("tr", {}, el("td", {}, k), el("td", {}, typeof v === "string" ? v : JSON.stringify(v)))));
  async function loadRecon() {
    let r;
    try { r = await api("/api/recon"); } catch { return; }
    $("#recon-card").hidden = false;
    $("#recon").replaceChildren(
      el("p", { class: "hint" }, `Generated ${r.generated_at} (${r.mode}). A red check means that selector found nothing.`),
      ...r.pages.map((pg) => {
        const checks = el("div", { class: "checks" }, Object.entries(pg.checks || {}).map(([k, v]) =>
          el("span", { class: "check " + (v ? "ok" : "bad") }, `${k}${typeof v === "number" ? ": " + v : ""}`)));
        const main = pg.kind === "details"
          ? { title: pg.title, image_id: pg.image_id, breadcrumbs: pg.breadcrumbs, "mapped fields": pg.fields,
              "audio URLs": pg.audio_urls, "label/image URLs": pg.image_urls, classification: pg.record,
              "path preview": pg.path_preview, "parser notes": pg.notes }
          : { name: pg.name, cat_id: pg.cat_id, [`subcategories (${pg.subcategories.length})`]: pg.subcategories.slice(0, 15),
              [`items (${pg.items.length})`]: pg.items.slice(0, 10), "parser notes": pg.notes };
        return el("div", { class: "recon-page" }, el("h4", {}, `${pg.source} · ${pg.kind} · ${pg.url}`), checks, kvTable(main));
      }));
  }

  // --- last-run results --------------------------------------------------------------------------
  async function loadJobItems() {
    let rows;
    try { rows = await api("/api/job/items"); } catch { return; }
    $("#test-card").hidden = false;
    if (!rows.length) { $("#test-results").replaceChildren(el("p", { class: "hint" }, "No items were touched by the last run.")); return; }
    $("#test-results").replaceChildren(el("table", { class: "results" },
      el("tr", {}, ["ID", "Title", "Language", "Status", "Verification"].map((h) => el("th", {}, h))),
      rows.slice(-50).map((r) => {
        const v = r.inspect;
        const ver = !v ? (r.last_error || "-")
          : v.valid ? `✓ ${v.container}${v.length ? `, ${v.length}s` : ""}${v.tags ? `, TLAN=${v.tags.TLAN || "?"}` : ""}${v.pdf ? ", PDF " + v.pdf : ""}`
          : `✗ ${v.error || "missing on disk"}`;
        return el("tr", {}, el("td", { class: "adm-path" }, r.id),
          el("td", {}, r.title, el("div", { class: "hint" }, r.artist || "")),
          el("td", {}, r.language, el("div", { class: "hint" }, r.method)),
          el("td", {}, r.status), el("td", { class: v && v.valid ? "ok" : v ? "bad" : "hint" }, ver));
      })));
  }

  // --- re-classifier (for the record whose story is open) -------------------------------------------
  let languages = null;
  async function renderReclassify() {
    const box = $("#reclassify");
    const it = app.focused();
    if (!it) { box.replaceChildren(el("p", { class: "hint" }, "No record selected yet.")); return; }
    if (it.status !== "COMPLETED") { box.replaceChildren(el("p", { class: "hint" }, `“${it.title}” is not downloaded yet.`)); return; }
    if (!languages) { try { languages = (await api("/api/options")).languages; } catch { languages = [it.language]; } }
    const lang = el("select", { class: "pf-input" }, languages.map((l) => el("option", { value: l, selected: l === it.language ? "" : null }, l)));
    const cat = el("input", { class: "pf-input", value: it.category || "" });
    const msg = el("div", { class: "recl-msg hint" }, `Currently ${it.language} via ${it.method}.`);
    const btn = el("button", { class: "pf-btn primary", onclick: async () => {
      btn.disabled = true; msg.textContent = "Rewriting tags and moving files…";
      try {
        const upd = await post(`/api/items/${encodeURIComponent(it.id)}/reclassify`, { language: lang.value, category: cat.value });
        msg.className = "recl-msg ok"; msg.textContent = `Saved: now ${upd.language} / ${upd.category} (MANUAL).`;
        await app.refresh();
      } catch (err) { msg.className = "recl-msg bad"; msg.textContent = err.message; }
      btn.disabled = false;
    } }, "Save override");
    box.replaceChildren(el("p", {}, el("strong", {}, it.title), it.artist ? ` · ${it.artist}` : ""),
      el("div", { class: "recl-grid" }, el("label", {}, "Language", lang), el("label", {}, "Category folder", cat), btn, msg));
  }
  window.addEventListener("patefon:focus", () => { if (drawer.classList.contains("open")) renderReclassify(); });

  poll();
  loadRecon();
})();
