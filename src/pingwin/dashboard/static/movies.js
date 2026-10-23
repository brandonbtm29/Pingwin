"use strict";

// Movie nights: history, ratings, watchlists and stats. Members see this; admins also get imports and approvals.
const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k in node && typeof v !== "string") node[k] = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}
function put(node, ...kids) { node.replaceChildren(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false)); }

function toast(message, isError = false) {
  const t = el("div", { class: "toast" + (isError ? " error" : "") }, message);
  $("toasts").append(t);
  setTimeout(() => t.remove(), isError ? 9000 : 5000);
}

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json", "X-Pingwin": "1" }, body: JSON.stringify(body),
  };
  const res = await fetch(path, { credentials: "same-origin", ...opts });
  let data = {};
  try { data = await res.json(); } catch { /* not JSON */ }
  if (res.status === 401) { window.location.reload(); }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

const fmt = (n) => (n === null || n === undefined ? "–" : Number(n).toFixed(1).replace(/\.0$/, ""));
const fmtDate = (d) => (d ? new Date(d + "T12:00:00").toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" }) : "–");
const movieLabel = (m) => (m.year ? `${m.title} (${m.year})` : m.title);

let data = null;

async function load() {
  try { data = await api("/api/movies"); } catch (e) { $("m-sub").textContent = e.message; return; }
  const v = data.viewer;
  $("m-sub").textContent = `${data.history.length} movie nights` + (data.history.length ? ` since ${fmtDate(data.history[data.history.length - 1].date || data.history.find((h) => h.date)?.date)}` : "");
  const box = $("m-viewer");
  if (v && v.kind === "discord") {
    box.hidden = false;
    put(box, v.avatar ? el("img", { class: "avatar", src: v.avatar, alt: "", width: "22", height: "22" }) : null, el("span", {}, v.name),
      el("button", { class: "btn small", onclick: async () => { try { await api("/api/signout", {}); } catch { /* ignore */ } window.location.reload(); } }, "Sign out"));
  }
  const admin = v && v.level === "admin";
  $("m-dashboard").hidden = !admin;
  $("m-admin-tab").hidden = !admin;
  $("o-note").textContent = data.omdb
    ? "Fills in poster, runtime, IMDb rating, director and stars for movies that don't have them (about 40 at a time)."
    : "Add an OMDb API key in the dashboard (Features → Movie night) to look up details.";
  $("o-go").disabled = !data.omdb;
  $("w-note").textContent = data.approvals && !admin ? "A mod approves suggestions before they join the list." : "";
  fillSeries();
  renderHistory();
  renderWatchlists();
  renderStats();
  renderPending();
}

function fillSeries() {
  const keep = $("h-series").value;
  put($("h-series"), el("option", { value: "" }, "All series"), [...new Set(data.history.map((h) => h.series).filter(Boolean))].map((s) => el("option", { value: s }, s)));
  $("h-series").value = keep;
  const w = $("w-series").value;
  put($("w-series"), data.series.map((s) => el("option", { value: s }, s)));
  if (w) $("w-series").value = w;
}

// ---------- history ----------
function matches(h, q) {
  if (!q) return true;
  const m = h.movie;
  return [m.title, m.year, m.director, m.stars, m.writer, h.series, h.date].join(" ").toLowerCase().includes(q);
}

function renderHistory() {
  const q = $("h-search").value.trim().toLowerCase();
  const series = $("h-series").value;
  let rows = data.history.filter((h) => (!series || h.series === series) && matches(h, q));
  const sort = $("h-sort").value;
  const val = (x) => (x === null || x === undefined ? -99 : x);
  if (sort === "ours") rows = [...rows].sort((a, b) => val(b.avg) - val(a.avg));
  if (sort === "imdb") rows = [...rows].sort((a, b) => val(b.movie.imdb_rating) - val(a.movie.imdb_rating));
  if (sort === "gap") rows = rows.filter((h) => h.avg !== null && h.movie.imdb_rating).sort((a, b) => (b.avg - b.movie.imdb_rating) - (a.avg - a.movie.imdb_rating));
  $("h-count").textContent = `${rows.length} of ${data.history.length}`;
  put($("h-rows"), rows.map((h) => el("tr", { class: "clickable", tabindex: "0", onclick: () => showMovie(h), onkeydown: (e) => { if (e.key === "Enter") showMovie(h); } },
    el("td", { class: "nowrap" }, fmtDate(h.date)),
    el("td", {}, el("strong", {}, h.movie.title), h.movie.year ? el("span", { class: "hint" }, ` ${h.movie.year}`) : null, h.open ? el("span", { class: "tag live" }, "rating now") : null),
    el("td", {}, h.series || ""),
    el("td", { class: "num" }, h.avg !== null ? `⭐ ${fmt(h.avg)}` : "–"),
    el("td", { class: "num" }, fmt(h.movie.imdb_rating)),
    el("td", { class: "num" }, h.movie.runtime || "–"))));
}

function showMovie(h) {
  const m = h.movie;
  const d = $("d-movie");
  const admin = data.viewer.level === "admin";
  const fix = el("details", { class: "fix-form" }, el("summary", {}, admin ? "Edit" : "Suggest a fix"),
    el("div", { class: "grid2" },
      el("label", {}, "Date", el("input", { id: "f-date", type: "date", value: h.date || "" })),
      el("label", {}, "Series", el("input", { id: "f-series", value: h.series || "" })),
      el("label", {}, "Title", el("input", { id: "f-title", value: m.title })),
      el("label", {}, "Year", el("input", { id: "f-year", value: m.year || "", inputmode: "numeric" }))),
    el("div", { class: "row-actions" },
      el("button", { class: "btn primary", onclick: async () => {
        const body = { kind: "edit", hid: h.id };
        const pairs = [["date", h.date || ""], ["series", h.series || ""], ["title", m.title], ["year", String(m.year || "")]];
        for (const [k, old] of pairs) { const v = $(`f-${k}`).value.trim(); if (v !== old) body[k] = v; }
        if (Object.keys(body).length === 2) return toast("Nothing changed.", true);
        try { toast((await api("/api/movies/suggest", body)).message); d.close(); await load(); } catch (e) { toast(e.message, true); }
      } }, admin ? "Save" : "Send to the mods"),
      admin ? el("button", { class: "btn danger", onclick: async () => {
        if (!window.confirm(`Remove ${movieLabel(m)} on ${fmtDate(h.date)} from the history?`)) return;
        try { toast((await api("/api/movies/admin", { action: "delete_entry", hid: h.id })).message); d.close(); await load(); } catch (e) { toast(e.message, true); }
      } }, "Remove from history") : null));
  put(d,
    el("div", { class: "dialog-head" }, el("h2", {}, movieLabel(m)), el("button", { class: "btn icon", "aria-label": "Close", onclick: () => d.close() }, "✕")),
    el("div", { class: "movie-detail" },
      m.poster ? el("img", { class: "poster", src: m.poster, alt: "", loading: "lazy" }) : null,
      el("dl", { class: "kv" },
        el("dt", {}, "Watched"), el("dd", {}, `${fmtDate(h.date)} · ${h.series || ""}`),
        el("dt", {}, "Our rating"), el("dd", {}, h.avg !== null ? `⭐ ${fmt(h.avg)} / 10` : "–"),
        el("dt", {}, "IMDb"), el("dd", {}, fmt(m.imdb_rating)),
        m.runtime ? [el("dt", {}, "Runtime"), el("dd", {}, `${m.runtime} min`)] : null,
        m.director ? [el("dt", {}, "Director"), el("dd", {}, m.director)] : null,
        m.writer ? [el("dt", {}, "Writer"), el("dd", {}, m.writer)] : null,
        m.stars ? [el("dt", {}, "Stars"), el("dd", {}, m.stars)] : null,
        m.genre ? [el("dt", {}, "Genre"), el("dd", {}, m.genre)] : null),
      m.plot ? el("p", { class: "plot" }, m.plot) : null),
    el("h3", {}, h.ratings.length ? `Ratings (${h.ratings.length})` : "No ratings yet"),
    el("ul", { class: "ratings" }, h.ratings.map((r) => el("li", {},
      el("div", { class: "r-head" }, el("strong", {}, r.name), el("span", { class: "score" }, r.score === null || r.score === undefined ? "–" : `${fmt(r.score)}/10`),
        admin ? el("button", { class: "btn icon", title: "Remove this rating", "aria-label": "Remove rating", onclick: async () => {
          if (!window.confirm(`Remove ${r.name}'s rating?`)) return;
          try { toast((await api("/api/movies/admin", { action: "delete_rating", hid: h.id, name: r.name })).message); d.close(); await load(); } catch (e) { toast(e.message, true); }
        } }, "✕") : null),
      r.comment ? el("p", {}, r.comment) : null,
      r.answered ? el("p", { class: "qa" }, el("em", {}, r.answered), r.answer ? ` → ${r.answer}` : "") : null,
      r.ask ? el("p", { class: "qa ask" }, "Asked the next person: ", el("em", {}, r.ask)) : null))),
    fix);
  d.showModal();
}

// ---------- watchlists ----------
function renderWatchlists() {
  put($("w-lists"), data.watchlists.map((w) => {
    const waiting = w.items.filter((i) => !i.watched);
    return el("div", { class: "panel" },
      el("h2", {}, `${w.series} watchlist`, el("span", { class: "hint" }, ` ${waiting.length} waiting`)),
      waiting.length ? el("ul", { class: "watch" }, waiting.map((i) => el("li", {},
        i.poster ? el("img", { class: "mini-poster", src: i.poster, alt: "", loading: "lazy" }) : el("span", { class: "mini-poster blank" }, "🎬"),
        el("div", {}, el("strong", {}, i.year ? `${i.title} (${i.year})` : i.title),
          el("div", { class: "hint" }, `suggested by ${i.by}`, i.runtime ? ` · ${i.runtime} min` : "", i.imdb_rating ? ` · IMDb ${fmt(i.imdb_rating)}` : ""))))) :
        el("p", { class: "hint" }, "Nothing waiting. Suggest one above!"));
  }));
}

$("w-add").addEventListener("click", async () => {
  const title = $("w-title").value.trim();
  if (!title) return toast("Type a title first.", true);
  try {
    const res = await api("/api/movies/suggest", { kind: "nominate", series: $("w-series").value, title, year: $("w-year").value.trim() });
    toast(res.message.replace(/\*\*/g, ""));
    $("w-title").value = ""; $("w-year").value = "";
    await load();
  } catch (e) { toast(e.message, true); }
});

// ---------- stats ----------
function topList(id, rows, text) { put($(id), rows.length ? rows.map((r) => el("li", {}, text(r))) : [el("li", { class: "hint" }, "Not enough ratings yet.")]); }

function renderStats() {
  const h = data.history;
  const rated = h.filter((x) => x.avg !== null && x.ratings.filter((r) => r.score !== null).length >= 3);
  const bySeries = {};
  for (const x of h) bySeries[x.series || "Other"] = (bySeries[x.series || "Other"] || 0) + 1;
  const minutes = h.reduce((t, x) => t + (x.movie.runtime || 0), 0);
  put($("s-cards"),
    el("div", { class: "card" }, el("div", { class: "label" }, "Movie nights"), el("div", { class: "value" }, h.length)),
    el("div", { class: "card" }, el("div", { class: "label" }, "Hours watched"), el("div", { class: "value" }, Math.round(minutes / 60))),
    ...Object.entries(bySeries).sort((a, b) => b[1] - a[1]).slice(0, 2).map(([s, n]) => el("div", { class: "card" }, el("div", { class: "label" }, s), el("div", { class: "value" }, n))));
  const lbl = (x) => [el("strong", {}, movieLabel(x.movie)), ` · ⭐ ${fmt(x.avg)}`];
  topList("s-top", [...rated].sort((a, b) => b.avg - a.avg).slice(0, 10), lbl);
  topList("s-bottom", [...rated].sort((a, b) => a.avg - b.avg).slice(0, 10), lbl);
  const gaps = h.filter((x) => x.avg !== null && x.movie.imdb_rating).sort((a, b) => Math.abs(b.avg - b.movie.imdb_rating) - Math.abs(a.avg - a.movie.imdb_rating)).slice(0, 10);
  topList("s-gap", gaps, (x) => [el("strong", {}, movieLabel(x.movie)), ` · us ${fmt(x.avg)} vs IMDb ${fmt(x.movie.imdb_rating)}`]);
  const raters = {};
  for (const x of h) for (const r of x.ratings) if (r.score !== null && r.score !== undefined) { raters[r.name] = raters[r.name] || { n: 0, sum: 0 }; raters[r.name].n++; raters[r.name].sum += r.score; }
  topList("s-raters", Object.entries(raters).sort((a, b) => b[1].n - a[1].n).slice(0, 10), ([name, s]) => [el("strong", {}, name), ` · ${s.n} ratings, average ${fmt(s.sum / s.n)}`]);
  const dirs = {};
  for (const x of h) for (const dn of (x.movie.director || "").split(",").map((s) => s.trim()).filter(Boolean)) dirs[dn] = (dirs[dn] || 0) + 1;
  topList("s-directors", Object.entries(dirs).filter(([, n]) => n > 1).sort((a, b) => b[1] - a[1]).slice(0, 10), ([name, n]) => [el("strong", {}, name), ` · ${n} movies`]);
  const years = {};
  for (const x of h) if (x.date) years[x.date.slice(0, 4)] = (years[x.date.slice(0, 4)] || 0) + 1;
  topList("s-years", Object.entries(years).sort(), ([y, n]) => [el("strong", {}, y), ` · ${n}`]);
}

// ---------- pending (mods approve here too) ----------
const KIND = { nominate: "Add to watchlist", remove: "Remove from watchlist", watched: "Watched mark", tonight: "Tonight's movie", edit: "History fix" };
function renderPending() {
  const p = data.pending || [];
  $("m-pending").hidden = !p.length;
  const admin = data.viewer.level === "admin";
  put($("m-pending-list"), p.map((r) => el("li", {},
    el("span", {}, el("strong", {}, KIND[r.kind] || r.kind), ` · ${r.series || ""} · `, r.data.title || r.data.label || "", r.data.changes ? ` (${Object.entries(r.data.changes).map(([k, v]) => `${k}: ${v}`).join(", ")})` : "", el("span", { class: "hint" }, ` asked by ${r.by_name}`)),
    admin ? el("span", { class: "row-btns" },
      el("button", { class: "btn small primary", onclick: () => decide(r.id, true) }, "Approve"),
      el("button", { class: "btn small danger", onclick: () => decide(r.id, false) }, "Deny")) : el("span", { class: "hint" }, "waiting"))));
}
async function decide(id, approve) {
  try { toast((await api("/api/movies/admin", { action: "decide", id, approve })).message.replace(/\*\*/g, "")); await load(); } catch (e) { toast(e.message, true); }
}

// ---------- admin ----------
async function readFile(input) { return input.files && input.files[0] ? await input.files[0].text() : ""; }
$("i-go").addEventListener("click", async () => {
  const btn = $("i-go");
  btn.disabled = true;
  $("i-result").textContent = "Importing…";
  try {
    const body = { action: "import", sheet_url: $("i-sheet").value.trim(), doc_url: $("i-doc").value.trim(),
      sheet_csv: await readFile($("i-sheet-file")), doc_text: await readFile($("i-doc-file")) };
    const res = await api("/api/movies/admin", body);
    $("i-result").textContent = res.message;
    await load();
  } catch (e) { $("i-result").textContent = ""; toast(e.message, true); }
  finally { btn.disabled = false; }
});
$("o-go").addEventListener("click", async () => {
  const btn = $("o-go");
  btn.disabled = true;
  $("o-result").textContent = "Looking up…";
  try { $("o-result").textContent = (await api("/api/movies/admin", { action: "lookup" })).message; await load(); }
  catch (e) { $("o-result").textContent = ""; toast(e.message, true); }
  finally { btn.disabled = false; }
});

// ---------- tabs ----------
function switchTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  document.querySelectorAll(".tab").forEach((s) => { s.hidden = s.id !== `tab-${name}`; });
  try { localStorage.setItem("pingwin-movies-tab", name); } catch { /* ignore */ }
}
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
for (const id of ["h-search", "h-series", "h-sort"]) $(id).addEventListener("input", renderHistory);
$("d-movie").addEventListener("click", (e) => { if (e.target === $("d-movie")) $("d-movie").close(); });

let saved = null;
try { saved = localStorage.getItem("pingwin-movies-tab"); } catch { /* ignore */ }
switchTab(saved && $(`tab-${saved}`) && saved !== "admin" ? saved : "history");
load();
