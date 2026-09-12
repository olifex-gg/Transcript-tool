/* YouTube Transcript – tiny vanilla JS front end. */
(() => {
  "use strict";
  const $ = (sel, el = document) => el.querySelector(sel);
  const state = { config: null, job: null, pollTimer: null, cache: new Map() };
  const YT_URL_RE = /https?:\/\/[^\s<>"']+/g;

  // ------------------------------------------------------------ helpers
  async function api(path, opts = {}) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    const isJson = (res.headers.get("content-type") || "").includes("application/json");
    const body = isJson ? await res.json() : await res.text();
    if (!res.ok) throw new Error((body && body.error) || (typeof body === "string" ? body : res.statusText));
    return body;
  }
  function toast(msg, ms = 2200) {
    const el = $("#toast");
    el.textContent = msg; el.hidden = false;
    clearTimeout(toast.t); toast.t = setTimeout(() => (el.hidden = true), ms);
  }
  function fmtDuration(s) {
    if (s == null || isNaN(s)) return "";
    s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
  }
  function relTime(iso) {
    const d = (Date.now() - new Date(iso).getTime()) / 1000;
    if (d < 60) return "just now";
    if (d < 3600) return `${Math.floor(d / 60)} min ago`;
    if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
    return new Date(iso).toLocaleDateString();
  }
  function el(tag, attrs = {}, ...children) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") e.className = v;
      else if (k === "hidden") e.hidden = !!v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else if (v != null) e.setAttribute(k, v);
    }
    for (const c of children) if (c != null) e.append(c.nodeType ? c : document.createTextNode(String(c)));
    return e;
  }
  async function copyText(getText) {
    // Safari only allows clipboard writes inside a user gesture; passing a
    // promise into ClipboardItem keeps the gesture alive while we fetch.
    try {
      if (navigator.clipboard && window.ClipboardItem && ClipboardItem.supports?.("text/plain") !== false) {
        const item = new ClipboardItem({ "text/plain": Promise.resolve(getText()).then(t => new Blob([t], { type: "text/plain" })) });
        await navigator.clipboard.write([item]);
        return true;
      }
    } catch (_) { /* fall through */ }
    try {
      const text = await getText();
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) { return false; }
  }
  async function shareText(title, getText, filename) {
    if (!navigator.share) return false;
    const text = await getText();
    try {
      if (filename && navigator.canShare) {
        const file = new File([text], filename, { type: "text/plain" });
        if (navigator.canShare({ files: [file] })) { await navigator.share({ title, files: [file] }); return true; }
      }
      await navigator.share({ title, text });
      return true;
    } catch (e) { return e && e.name === "AbortError"; }
  }
  const prefs = {
    get() { try { return JSON.parse(localStorage.getItem("yt-transcript-prefs") || "{}"); } catch { return {}; } },
    set(p) { try { localStorage.setItem("yt-transcript-prefs", JSON.stringify({ ...prefs.get(), ...p })); } catch { /* ignore */ } },
  };

  // ------------------------------------------------------------ home view
  async function showHome() {
    $("#view-home").hidden = false; $("#view-job").hidden = true;
    document.title = "YouTube Transcript";
    const p = prefs.get();
    $("#opt-language").value = p.language ?? (state.config?.default_language || "en");
    $("#opt-engine").value = p.engine ?? (state.config?.default_engine || "auto");
    $("#opt-playlist").checked = p.expand_playlists ?? (state.config?.expand_playlists ?? true);
    if (state.config && !state.config.whisper_available) {
      $("#engine-hint").textContent = "Whisper is not installed on this server; videos without captions will fail.";
    } else if (state.config) {
      $("#engine-hint").textContent = `Whisper model: ${state.config.whisper_model}. Slower, but works for videos with no captions.`;
    }
    for (const o of document.querySelectorAll(".origin")) o.textContent = location.origin;

    const params = new URLSearchParams(location.search);
    if (params.get("error") === "no-url") showFormError("That share didn't contain a link I could use.");
    const shared = [params.get("url"), params.get("text"), params.get("title")].filter(Boolean).join(" ");
    const urls = shared.match(YT_URL_RE) || [];
    if (params.toString()) history.replaceState(null, "", "/");
    if (urls.length) {
      $("#urls").value = urls.join("\n");
      submit();  // shared from another app: go straight to work
    }
    loadRecent();
  }
  function showFormError(msg) { const e = $("#form-error"); e.textContent = msg; e.hidden = !msg; }

  async function submit() {
    const raw = $("#urls").value.trim();
    const urls = raw.match(YT_URL_RE) || raw.split(/\s+/).filter(Boolean);
    if (!urls.length) return showFormError("Paste a YouTube link first.");
    showFormError("");
    const opts = {
      language: $("#opt-language").value.trim() || "en",
      engine: $("#opt-engine").value,
      expand_playlists: $("#opt-playlist").checked,
    };
    prefs.set(opts);
    const btn = $("#submit-btn"); btn.disabled = true; btn.textContent = "Starting…";
    try {
      const job = await api("/api/jobs", { method: "POST", body: JSON.stringify({ urls, ...opts }) });
      navigate(`/jobs/${job.id}`);
    } catch (e) {
      showFormError(e.message || String(e));
    } finally { btn.disabled = false; btn.textContent = "Transcribe"; }
  }

  async function loadRecent() {
    let jobs = [];
    try { jobs = (await api("/api/jobs?limit=30")).jobs; } catch { return; }
    const list = $("#recent-list"); list.replaceChildren();
    $("#recent").hidden = !jobs.length;
    for (const j of jobs) {
      const c = j.counts || {};
      const meta = [j.status, c.total ? `${c.done}/${c.total} videos` : null, relTime(j.created_at)].filter(Boolean).join(" · ");
      list.append(el("li", { class: "card" },
        el("a", { class: "recent-item", href: `/jobs/${j.id}`, onclick: ev => { ev.preventDefault(); navigate(`/jobs/${j.id}`); } },
          el("span", { class: `pill ${j.status}` }, j.status),
          el("span", {}, el("div", { class: "title" }, j.title), el("div", { class: "meta" }, meta)),
          el("button", { class: "btn small del", type: "button", onclick: async ev => {
            ev.preventDefault(); ev.stopPropagation();
            if (!confirm("Delete this job and its transcripts?")) return;
            await api(`/api/jobs/${j.id}`, { method: "DELETE" }); loadRecent();
          } }, "Delete"))));
    }
  }

  // ------------------------------------------------------------ job view
  function jobId() { return location.pathname.split("/")[2]; }
  function fmtOpts() { return { fmt: $("#fmt").value, ts: $("#timestamps").checked ? "1" : "0" }; }
  function itemUrl(index, download) {
    const { fmt, ts } = fmtOpts();
    return `/api/jobs/${jobId()}/items/${index}?format=${fmt}&timestamps=${ts}${download ? "&download=1" : ""}`;
  }
  async function itemText(index) {
    const key = `${index}:${fmtOpts().fmt}:${fmtOpts().ts}`;
    if (!state.cache.has(key)) state.cache.set(key, await api(itemUrl(index, false)));
    return state.cache.get(key);
  }
  async function combinedText() {
    const { fmt, ts } = fmtOpts();
    return api(`/api/jobs/${jobId()}/combined?format=${fmt === "md" ? "md" : "txt"}&timestamps=${ts}`);
  }

  async function showJob() {
    $("#view-home").hidden = true; $("#view-job").hidden = false;
    state.cache.clear();
    const p = prefs.get();
    $("#fmt").value = p.fmt || "txt"; $("#timestamps").checked = !!p.ts;
    $("#share-all").hidden = !navigator.share;
    await refreshJob();
  }
  async function refreshJob() {
    clearTimeout(state.pollTimer);
    let job;
    try { job = await api(`/api/jobs/${jobId()}`); }
    catch (e) { $("#job-title").textContent = "Job not found"; $("#job-error").textContent = e.message; $("#job-error").hidden = false; return; }
    state.job = job; renderJob(job);
    if (["queued", "resolving", "running"].includes(job.status)) state.pollTimer = setTimeout(refreshJob, 2000);
  }
  function renderJob(job) {
    const c = job.counts;
    document.title = `${job.title} – Transcript`;
    $("#job-title").textContent = job.title;
    const st = $("#job-status"); st.textContent = job.status; st.className = `pill ${job.status}`;
    const finished = c.done + c.failed + c.skipped;
    $("#job-progress-text").textContent = c.total ? `${c.done} of ${c.total} transcribed${c.failed ? `, ${c.failed} failed` : ""}` : (job.status === "resolving" ? "Looking up videos…" : "");
    $("#job-progress").value = c.total ? finished / c.total : 0;
    $("#job-error").textContent = job.error || ""; $("#job-error").hidden = !job.error;
    const active = ["queued", "resolving", "running"].includes(job.status);
    $("#cancel-btn").hidden = !active;
    $("#retry-btn").hidden = !(c.failed || c.skipped || (job.status === "failed" && !c.total));
    $("#dl-zip").hidden = c.total < 2;
    const anyDone = c.done > 0;
    for (const id of ["copy-all", "share-all", "dl-combined", "dl-zip"]) $(`#${id}`).toggleAttribute("disabled", !anyDone);
    updateLinks();

    const list = $("#items");
    const open = new Set([...list.querySelectorAll("li[data-open='1']")].map(li => li.dataset.index));
    list.replaceChildren();
    for (const it of job.items) list.append(renderItem(it, job.items.length, open.has(String(it.index))));
  }
  function updateLinks() {
    const { fmt, ts } = fmtOpts();
    $("#dl-zip").href = `/api/jobs/${jobId()}/zip?format=${fmt}&timestamps=${ts}`;
    $("#dl-combined").href = `/api/jobs/${jobId()}/combined?format=${fmt === "md" ? "md" : "txt"}&timestamps=${ts}&download=1`;
    for (const a of document.querySelectorAll("a[data-dl]")) a.href = itemUrl(a.dataset.dl, true);
  }
  function renderItem(it, total, open) {
    const meta = [fmtDuration(it.duration), it.source, it.language, it.words ? `${it.words.toLocaleString()} words` : null].filter(Boolean).join(" · ");
    const pre = el("pre", { class: "transcript", hidden: !open });
    const li = el("li", { class: "card", "data-index": it.index, "data-open": open ? "1" : "0" });
    const viewBtn = el("button", { class: "btn small", type: "button", onclick: async () => {
      if (pre.hidden) { pre.textContent = "Loading…"; pre.hidden = false; li.dataset.open = "1"; viewBtn.textContent = "Hide";
        try { pre.textContent = await itemText(it.index); } catch (e) { pre.textContent = e.message; } }
      else { pre.hidden = true; li.dataset.open = "0"; viewBtn.textContent = "View"; }
    } }, open ? "Hide" : "View");
    if (open) itemText(it.index).then(t => (pre.textContent = t)).catch(e => (pre.textContent = e.message));
    const actions = it.status === "done" ? el("div", { class: "item-actions" },
      viewBtn,
      el("button", { class: "btn small", type: "button", onclick: async () => toast((await copyText(() => itemText(it.index))) ? "Copied" : "Copy failed – open View and select the text") }, "Copy"),
      navigator.share ? el("button", { class: "btn small", type: "button", onclick: () => shareText(it.title, () => itemText(it.index), `${it.title}.${fmtOpts().fmt}`) }, "Share") : null,
      el("a", { class: "btn small", href: itemUrl(it.index, true), "data-dl": it.index, download: "" }, "Download"),
      el("a", { class: "btn small", href: it.url, target: "_blank", rel: "noopener" }, "YouTube"),
    ) : el("div", { class: "item-actions" }, el("a", { class: "btn small", href: it.url, target: "_blank", rel: "noopener" }, "YouTube"));
    li.append(
      el("div", { class: "item-head" },
        el("div", { class: "item-idx" }, total > 1 ? it.index + 1 : "▶"),
        el("div", { class: "item-main" },
          el("div", { class: "item-title" }, it.title || it.video_id || it.url),
          el("div", { class: "item-meta" }, meta),
          it.message && it.status === "running" ? el("div", { class: "item-msg" }, it.message + "…") : null,
          it.error ? el("div", { class: "item-err" }, it.error) : null),
        el("span", { class: `pill ${it.status}` }, it.status)),
      actions, pre);
    return li;
  }

  // ------------------------------------------------------------ wiring
  function navigate(path) { history.pushState(null, "", path); route(); }
  async function route() {
    clearTimeout(state.pollTimer);
    if (location.pathname.startsWith("/jobs/")) showJob(); else showHome();
  }
  $("#form").addEventListener("submit", ev => { ev.preventDefault(); submit(); });
  $("#urls").addEventListener("keydown", ev => { if (ev.key === "Enter" && !ev.shiftKey && !ev.ctrlKey) { ev.preventDefault(); submit(); } });
  $("#paste-btn").addEventListener("click", async () => {
    try { const t = await navigator.clipboard.readText(); if (t) { $("#urls").value = t.trim(); submit(); } else toast("Clipboard is empty"); }
    catch { toast("Paste blocked by the browser – long-press the box instead"); $("#urls").focus(); }
  });
  $("#refresh-recent").addEventListener("click", loadRecent);
  $("#fmt").addEventListener("change", () => { prefs.set({ fmt: $("#fmt").value }); state.cache.clear(); updateLinks(); if (state.job) renderJob(state.job); });
  $("#timestamps").addEventListener("change", () => { prefs.set({ ts: $("#timestamps").checked }); state.cache.clear(); updateLinks(); if (state.job) renderJob(state.job); });
  $("#copy-all").addEventListener("click", async () => toast((await copyText(combinedText)) ? "Copied all transcripts" : "Copy failed"));
  $("#share-all").addEventListener("click", () => shareText(state.job?.title || "Transcript", combinedText, `${state.job?.title || "transcript"}.${fmtOpts().fmt === "md" ? "md" : "txt"}`));
  $("#retry-btn").addEventListener("click", async () => { await api(`/api/jobs/${jobId()}/retry`, { method: "POST" }); refreshJob(); });
  $("#cancel-btn").addEventListener("click", async () => { await api(`/api/jobs/${jobId()}/cancel`, { method: "POST" }); refreshJob(); });
  $("#delete-btn").addEventListener("click", async () => {
    if (!confirm("Delete this job and its transcripts?")) return;
    await api(`/api/jobs/${jobId()}`, { method: "DELETE" }); navigate("/");
  });
  window.addEventListener("popstate", route);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && location.pathname.startsWith("/jobs/")) refreshJob(); });
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

  (async () => {
    try { state.config = await api("/api/config"); $("#whisper-badge").hidden = !state.config.whisper_available; } catch { /* offline */ }
    route();
  })();
})();
