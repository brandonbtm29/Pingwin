"use strict";

// Movie nights: history, ratings, watchlists and stats. Members see this; admins also get imports and approvals.
const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "style") {  // CSP allows the style API, not style="" attributes
      for (const decl of String(v).split(";")) { const i = decl.indexOf(":"); if (i > 0) node.style.setProperty(decl.slice(0, i).trim(), decl.slice(i + 1).trim()); }
    }
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

// Small visual helpers: a steady color per name, initials avatars, and score badges.
function hueOf(text) { let h = 0; for (const ch of String(text || "")) h = (h * 31 + ch.codePointAt(0)) % 360; return h; }
function initials(name) { const w = String(name || "?").replace(/[^\p{L}\p{N} ]/gu, " ").trim().split(/\s+/); return ((w[0] || "?")[0] + (w.length > 1 ? w[w.length - 1][0] : "")).toUpperCase(); }
const avatar = (name) => el("span", { class: "avatar-i", style: `--h:${hueOf(name)}`, "aria-hidden": "true" }, initials(name));
function scoreBadge(score, suffix = "") {
  if (typeof score !== "number") return el("span", { class: "score-badge none" }, "–");
  const tier = score >= 8 ? "hi" : score >= 6 ? "mid" : score >= 4 ? "low" : "bad";
  return el("span", { class: `score-badge ${tier}` }, fmt(score) + suffix);
}
function posterArt(m, cls = "poster-art") {
  if (m.poster) return el("img", { class: cls, src: m.poster, alt: "", loading: "lazy" });
  return el("div", { class: `${cls} blank`, style: `--h:${hueOf(m.title)}`, "aria-hidden": "true" }, el("span", {}, m.title));
}

let data = null;

async function load() {
  try { data = await api("/api/movies"); } catch (e) { $("m-sub").textContent = e.message; return; }
  const v = data.viewer;
  $("m-sub").textContent = `${data.history.length} movie nights` + (data.history.length ? ` since ${fmtDate(data.history[data.history.length - 1].date || data.history.find((h) => h.date)?.date)}` : "");
  const nReviews = data.history.reduce((t, h) => t + h.ratings.length, 0);
  const nPeople = new Set(data.history.flatMap((h) => h.ratings.map((r) => (r.who || "").toLowerCase()))).size;
  const hours = Math.round(data.history.reduce((t, h) => t + (h.movie.runtime || 0), 0) / 60);
  const waiting = data.watchlists.reduce((t, w) => t + w.items.filter((i) => !i.watched).length, 0);
  put($("m-stats"), [["Movie nights", data.history.length], ["Reviews", nReviews], ["Critics", nPeople], ["Hours watched", hours], ["On the watchlists", waiting]]
    .map(([label, n]) => el("div", { class: "hero-stat" }, el("strong", {}, n.toLocaleString()), el("span", {}, label))));
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
  fillPeople();
  renderHistory();
  renderReviews();
  if (admin) renderPeopleAdmin();
  renderWatchlists();
  renderStats();
  renderPending();
}

function fillSeries() {
  const keep = $("h-series").value;
  put($("h-series"), el("option", { value: "" }, "All series"), [...new Set(data.history.map((h) => h.series).filter(Boolean))].map((s) => el("option", { value: s }, s)));
  $("h-series").value = keep;
  for (const id of ["w-series", "l-series"]) {
    const w = $(id).value;
    put($(id), data.series.map((x) => el("option", { value: x }, x)));
    if (w) $(id).value = w;
  }
}

// ---------- history ----------
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private window: fine */ } },
};
const nul = (x) => (x === null || x === undefined || x === "" ? null : x);
const gapOf = (h) => (h.avg !== null && h.movie.imdb_rating ? h.avg - h.movie.imdb_rating : null);
const signed = (n) => (n === null ? "–" : (n > 0 ? "+" : "") + fmt(n));
const COLS = [
  { key: "date", label: "Date", on: true, cls: "nowrap", cell: (h) => fmtDate(h.date), sort: (h) => nul(h.date) },
  { key: "movie", label: "Movie", on: true, fixed: true, sort: (h) => h.movie.title.toLowerCase(),
    cell: (h) => el("div", { class: "movie-cell" }, h.movie.poster ? el("img", { class: "thumb", src: h.movie.poster, alt: "", loading: "lazy" }) : null,
      el("div", {}, el("strong", {}, h.movie.title), h.movie.year ? el("span", { class: "hint" }, ` ${h.movie.year}`) : null, h.open ? el("span", { class: "tag live" }, "rating now") : null)) },
  { key: "year", label: "Year", num: true, cell: (h) => h.movie.year || "–", sort: (h) => nul(h.movie.year) },
  { key: "series", label: "Series", on: true, cell: (h) => h.series || "", sort: (h) => nul(h.series) },
  { key: "ours", label: "Ours", on: true, num: true, cell: (h) => scoreBadge(h.avg), sort: (h) => h.avg },
  { key: "imdb", label: "IMDb", on: true, num: true, cell: (h) => fmt(h.movie.imdb_rating), sort: (h) => nul(h.movie.imdb_rating) },
  { key: "gap", label: "Us vs IMDb", num: true, cell: (h) => signed(gapOf(h)), sort: gapOf },
  { key: "count", label: "Ratings", num: true, cell: (h) => h.ratings.length || "–", sort: (h) => h.ratings.length },
  { key: "runtime", label: "Min", on: true, num: true, cell: (h) => h.movie.runtime || "–", sort: (h) => nul(h.movie.runtime) },
  { key: "director", label: "Director", cls: "people", cell: (h) => h.movie.director || "–", sort: (h) => nul(h.movie.director) },
  { key: "writer", label: "Writers", cls: "people", cell: (h) => h.movie.writer || "–", sort: (h) => nul(h.movie.writer) },
  { key: "stars", label: "Stars", cls: "people", cell: (h) => h.movie.stars || "–", sort: (h) => nul(h.movie.stars) },
  { key: "genre", label: "Genre", cls: "people", cell: (h) => h.movie.genre || "–", sort: (h) => nul(h.movie.genre) },
];
let shownCols = store.get("pingwin-movies-cols", COLS.filter((c) => c.on).map((c) => c.key));
let histSort = { key: "date", dir: "desc" };

function renderColumnPicker() {
  put($("h-cols"), COLS.filter((c) => !c.fixed).map((c) => el("label", { class: "check" },
    el("input", { type: "checkbox", checked: shownCols.includes(c.key), onchange: (e) => {
      shownCols = e.target.checked ? COLS.map((x) => x.key).filter((k) => k === c.key || shownCols.includes(k)) : shownCols.filter((k) => k !== c.key);
      store.set("pingwin-movies-cols", shownCols);
      renderHistory();
    } }), c.label)),
  el("button", { class: "btn small", onclick: () => { shownCols = COLS.filter((c) => c.on).map((c) => c.key); store.set("pingwin-movies-cols", shownCols); renderColumnPicker(); renderHistory(); } }, "Reset"));
}

function matches(h, q) {
  if (!q) return true;
  const m = h.movie;
  return [m.title, m.year, m.director, m.stars, m.writer, m.genre, h.series, h.date].join(" ").toLowerCase().includes(q);
}

function sortRows(rows, getter, dir) {
  // Blanks always go last, whichever way you sort.
  const sign = dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = getter(a); const y = getter(b);
    if (x === null || x === undefined) return y === null || y === undefined ? 0 : 1;
    if (y === null || y === undefined) return -1;
    return sign * (typeof x === "string" ? x.localeCompare(y) : x - y);
  });
}

function renderHistory() {
  const q = $("h-search").value.trim().toLowerCase();
  const series = $("h-series").value;
  const cols = COLS.filter((c) => c.fixed || shownCols.includes(c.key));
  const col = COLS.find((c) => c.key === histSort.key) || COLS[0];
  const rows = sortRows(data.history.filter((h) => (!series || h.series === series) && matches(h, q)), col.sort, histSort.dir);
  const preset = `${histSort.key}:${histSort.dir}`;
  $("h-sort").value = [...$("h-sort").options].some((o) => o.value === preset) ? preset : "custom";
  $("h-count").textContent = `${rows.length} of ${data.history.length}`;
  put($("h-head"), cols.map((c) => el("th", { class: `c-${c.key}` + (c.num ? " num" : ""), "aria-sort": c.key === histSort.key ? (histSort.dir === "asc" ? "ascending" : "descending") : null },
    el("button", { class: "th-sort", onclick: () => {
      histSort = { key: c.key, dir: c.key === histSort.key ? (histSort.dir === "asc" ? "desc" : "asc") : (c.num || c.key === "date" ? "desc" : "asc") };
      renderHistory();
    } }, c.label, c.key === histSort.key ? (histSort.dir === "asc" ? " ▲" : " ▼") : ""))));
  put($("h-rows"), rows.map((h) => el("tr", { class: "clickable", tabindex: "0", onclick: () => showMovie(h), onkeydown: (e) => { if (e.key === "Enter") showMovie(h); } },
    cols.map((c) => el("td", { class: [`c-${c.key}`, c.num ? "num" : "", c.cls || ""].join(" ").trim() }, c.cell(h))))));
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
      posterArt(m, "poster"),
      el("dl", { class: "kv" },
        el("dt", {}, "Watched"), el("dd", {}, `${fmtDate(h.date)} · ${h.series || ""}`),
        el("dt", {}, "Our rating"), el("dd", {}, scoreBadge(h.avg, " / 10")),
        el("dt", {}, "IMDb"), el("dd", {}, fmt(m.imdb_rating)),
        m.runtime ? [el("dt", {}, "Runtime"), el("dd", {}, `${m.runtime} min`)] : null,
        m.director ? [el("dt", {}, "Director"), el("dd", {}, m.director)] : null,
        m.writer ? [el("dt", {}, "Writer"), el("dd", {}, m.writer)] : null,
        m.stars ? [el("dt", {}, "Stars"), el("dd", {}, m.stars)] : null,
        m.genre ? [el("dt", {}, "Genre"), el("dd", {}, m.genre)] : null),
      m.plot ? el("p", { class: "plot" }, m.plot) : null),
    el("h3", {}, h.ratings.length ? `Ratings (${h.ratings.length})` : "No ratings yet"),
    el("ul", { class: "ratings" }, h.ratings.map((r) => el("li", {},
      el("div", { class: "r-head" }, avatar(r.who), el("strong", {}, r.who), r.who !== r.name ? el("span", { class: "hint" }, `as ${r.name}`) : null, el("span", { class: "score" }, scoreBadge(r.score)),
        admin ? el("button", { class: "btn icon", title: "Remove this rating", "aria-label": "Remove rating", onclick: async () => {
          if (!window.confirm(`Remove ${r.who}'s rating?`)) return;
          try { toast((await api("/api/movies/admin", { action: "delete_rating", hid: h.id, key: r.key })).message); d.close(); await load(); } catch (e) { toast(e.message, true); }
        } }, "✕") : null),
      r.comment ? el("p", {}, r.comment) : null,
      r.answered ? el("p", { class: "qa" }, el("em", {}, r.answered), r.answer ? ` → ${r.answer}` : "") : null,
      r.ask ? el("p", { class: "qa ask" }, "Asked the next person: ", el("em", {}, r.ask)) : null))),
    fix);
  d.showModal();
}

// ---------- watchlists ----------
const listFilter = {};
function renderWatchlists() {
  put($("w-lists"), data.watchlists.map((w) => {
    const waiting = w.items.filter((i) => !i.watched);
    const lists = [...new Set(waiting.map((i) => i.list || "").filter(Boolean))].sort();
    const pick = listFilter[w.series] || "";
    const shown = waiting.filter((i) => !pick || (i.list || "") === pick);
    return el("div", { class: "panel" },
      el("div", { class: "w-head" },
        el("h2", {}, `${w.series} watchlist`, el("span", { class: "hint" }, ` ${waiting.length} waiting`)),
        lists.length ? el("div", { class: "chips", role: "group", "aria-label": "Lists" },
          ["", ...lists].map((l) => el("button", { class: "chip", "aria-pressed": String(pick === l), onclick: () => { listFilter[w.series] = l; renderWatchlists(); } },
            l || "All", el("span", { class: "n" }, l ? waiting.filter((i) => i.list === l).length : waiting.length)))) : null),
      shown.length ? el("ul", { class: "poster-grid" }, shown.map((i) => el("li", { class: "poster-card" },
        posterArt({ title: i.title, poster: i.poster }),
        el("div", { class: "pc-body" },
          el("strong", {}, i.title), i.year ? el("span", { class: "hint" }, ` ${i.year}`) : null,
          el("div", { class: "pc-meta" }, i.list ? el("span", { class: "chip small" }, i.list) : null, i.runtime ? `${i.runtime} min` : null, i.imdb_rating ? `IMDb ${fmt(i.imdb_rating)}` : null),
          i.note ? el("div", { class: "hint" }, i.note) : null,
          el("div", { class: "hint" }, `suggested by ${i.by}`))))) :
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
  const lbl = (x) => [el("button", { class: "linkish", onclick: () => showMovie(x) }, movieLabel(x.movie)), " ", scoreBadge(x.avg)];
  topList("s-top", [...rated].sort((a, b) => b.avg - a.avg).slice(0, 10), lbl);
  topList("s-bottom", [...rated].sort((a, b) => a.avg - b.avg).slice(0, 10), lbl);
  const gaps = h.filter((x) => x.avg !== null && x.movie.imdb_rating).sort((a, b) => Math.abs(b.avg - b.movie.imdb_rating) - Math.abs(a.avg - a.movie.imdb_rating)).slice(0, 10);
  topList("s-gap", gaps, (x) => [el("strong", {}, movieLabel(x.movie)), ` · us ${fmt(x.avg)} vs IMDb ${fmt(x.movie.imdb_rating)}`]);
  const raters = {};
  for (const x of h) for (const r of x.ratings) if (r.score !== null && r.score !== undefined) { raters[r.who] = raters[r.who] || { n: 0, sum: 0 }; raters[r.who].n++; raters[r.who].sum += r.score; }
  topList("s-raters", Object.entries(raters).sort((a, b) => b[1].n - a[1].n).slice(0, 10), ([name, s]) => [avatar(name), " ", el("strong", {}, name), ` · ${s.n} ratings `, scoreBadge(s.sum / s.n)]);
  const dirs = {};
  for (const x of h) for (const dn of (x.movie.director || "").split(",").map((s) => s.trim()).filter(Boolean)) dirs[dn] = (dirs[dn] || 0) + 1;
  topList("s-directors", Object.entries(dirs).filter(([, n]) => n > 1).sort((a, b) => b[1] - a[1]).slice(0, 10), ([name, n]) => [el("strong", {}, name), ` · ${n} movies`]);
  const years = {};
  for (const x of h) if (x.date) years[x.date.slice(0, 4)] = (years[x.date.slice(0, 4)] || 0) + 1;
  topList("s-years", Object.entries(years).sort(), ([y, n]) => [el("strong", {}, y), ` · ${n}`]);
}

// ---------- reviews ----------
const personKey = (name) => (name || "").trim().toLowerCase();
let reviewLimit = 60;

function allReviews() {
  const out = [];
  for (const h of data.history) for (const r of h.ratings) out.push({ ...r, h });
  return out;
}

function peopleSummary(reviews) {
  const people = new Map();
  for (const r of reviews) {
    const k = personKey(r.who);
    const p = people.get(k) || { name: r.who, n: 0, scored: 0, sum: 0, vs: 0, vsN: 0 };
    p.n++;
    if (typeof r.score === "number") {
      p.scored++; p.sum += r.score;
      if (r.h.avg !== null && r.h.ratings.length > 1) { p.vs += r.score - r.h.avg; p.vsN++; }
    }
    people.set(k, p);
  }
  return [...people.entries()].sort((a, b) => b[1].n - a[1].n);
}

function fillPeople() {
  const keep = $("r-person").value;
  const people = peopleSummary(allReviews());
  put($("r-person"), el("option", { value: "" }, "Everyone"), people.map(([k, p]) => el("option", { value: k }, `${p.name} (${p.n})`)));
  $("r-person").value = people.some(([k]) => k === keep) ? keep : "";
  const sk = $("r-series").value;
  put($("r-series"), el("option", { value: "" }, "All series"), [...new Set(data.history.map((h) => h.series).filter(Boolean))].map((x) => el("option", { value: x }, x)));
  $("r-series").value = sk;
}

function renderReviews() {
  const q = $("r-search").value.trim().toLowerCase();
  const who = $("r-person").value;
  const series = $("r-series").value;
  const band = $("r-score").value;
  const inBand = (sc) => band === "" || (typeof sc === "number" && (band === "8" ? sc >= 8 : band === "5" ? sc >= 5 && sc < 8 : sc < 5));
  const everyone = allReviews();
  let rows = everyone.filter((r) => (!who || personKey(r.who) === who) && (!series || r.h.series === series) && inBand(r.score)
    && (!q || [r.comment, r.answer, r.ask, r.answered, r.who, r.name, r.h.movie.title, r.h.movie.year].join(" ").toLowerCase().includes(q)));
  const sort = $("r-sort").value;
  const sc = (r) => (typeof r.score === "number" ? r.score : null);
  if (sort === "new") rows = sortRows(rows, (r) => nul(r.h.date), "desc");
  if (sort === "old") rows = sortRows(rows, (r) => nul(r.h.date), "asc");
  if (sort === "high") rows = sortRows(rows, sc, "desc");
  if (sort === "low") rows = sortRows(rows, sc, "asc");
  if (sort === "long") rows = sortRows(rows, (r) => (r.comment || "").length, "desc");
  $("r-count").textContent = `${rows.length} of ${everyone.length} reviews`;

  // Everyone at a glance (click a card to see just their reviews).
  const people = peopleSummary(everyone);
  $("r-people").hidden = !!who;
  put($("r-people"), who ? [] : people.map(([k, p]) => el("button", { class: "card person", onclick: () => { $("r-person").value = k; reviewLimit = 60; renderReviews(); } },
    avatar(p.name),
    el("div", {}, el("div", { class: "p-name" }, p.name), el("div", { class: "hint" }, `${p.n} review${p.n === 1 ? "" : "s"}`)),
    scoreBadge(p.scored ? p.sum / p.scored : null))));

  // One person's summary.
  const p = who ? people.find(([k]) => k === who) : null;
  $("r-summary").hidden = !p;
  if (p) {
    const s = p[1];
    const mine = everyone.filter((r) => personKey(r.who) === who && typeof r.score === "number");
    const best = sortRows(mine, sc, "desc").slice(0, 3);
    const worst = sortRows(mine.filter((r) => !best.includes(r)), sc, "asc").slice(0, 3);  // never the same as a favorite
    const line = (r) => el("li", {}, el("button", { class: "linkish", onclick: () => showMovie(r.h) }, movieLabel(r.h.movie)), " ", scoreBadge(r.score));
    put($("r-summary"),
      el("div", { class: "dialog-head" }, el("div", { class: "person-title" }, avatar(s.name), el("h2", {}, s.name)), el("button", { class: "btn small", onclick: () => { $("r-person").value = ""; renderReviews(); } }, "Everyone")),
      el("div", { class: "cards" },
        el("div", { class: "card" }, el("div", { class: "label" }, "Reviews"), el("div", { class: "value" }, s.n)),
        el("div", { class: "card" }, el("div", { class: "label" }, "Average score"), el("div", { class: "value" }, s.scored ? fmt(s.sum / s.scored) : "–")),
        el("div", { class: "card" }, el("div", { class: "label" }, "Compared with the group"), el("div", { class: "value" }, s.vsN ? signed(s.vs / s.vsN) : "–"))),
      el("div", { class: "grid2" },
        el("div", {}, el("h3", {}, "Favorites"), el("ol", {}, best.map(line))),
        worst.length ? el("div", {}, el("h3", {}, "Least favorites"), el("ol", {}, worst.map(line))) : null));
  }

  put($("r-list"), rows.slice(0, reviewLimit).map((r) => el("li", { class: "review", style: `--h:${hueOf(r.who)}` },
    el("div", { class: "r-head" }, avatar(r.who),
      el("div", { class: "r-who" }, el("strong", {}, r.who), r.who !== r.name ? el("span", { class: "hint" }, ` as ${r.name}`) : null),
      el("span", { class: "score" }, scoreBadge(r.score))),
    el("div", { class: "hint" }, el("button", { class: "linkish", onclick: () => showMovie(r.h) }, movieLabel(r.h.movie)), ` · ${fmtDate(r.h.date)}`, r.h.series ? ` · ${r.h.series}` : ""),
    r.comment ? el("p", {}, r.comment) : null,
    r.answered ? el("p", { class: "qa" }, el("em", {}, r.answered), r.answer ? ` → ${r.answer}` : "") : null,
    r.ask ? el("p", { class: "qa ask" }, "Asked the next person: ", el("em", {}, r.ask)) : null)));
  $("r-more").hidden = rows.length <= reviewLimit;
  $("r-more").textContent = `Show more (${rows.length - reviewLimit} left)`;
}
$("r-more").addEventListener("click", () => { reviewLimit += 60; renderReviews(); });
for (const id of ["r-search", "r-person", "r-series", "r-score", "r-sort"]) $(id).addEventListener("input", () => { reviewLimit = 60; renderReviews(); });

// ---------- watchlist import (admins) ----------
$("l-go").addEventListener("click", async () => {
  const btn = $("l-go");
  btn.disabled = true;
  $("l-result").textContent = "Reading the list…";
  try {
    const res = await api("/api/movies/admin", { action: "import_list", link: $("l-link").value.trim(), text: $("l-text").value, series: $("l-series").value, label: $("l-label").value.trim() });
    $("l-result").textContent = res.message;
    $("l-link").value = ""; $("l-text").value = "";
    await load();
  } catch (e) { $("l-result").textContent = ""; toast(e.message, true); }
  finally { btn.disabled = false; }
});

// ---------- people (admins) ----------
function renderPeopleAdmin() {
  const keys = new Map();
  for (const h of data.history) for (const r of h.ratings) {
    const k = keys.get(r.key) || { key: r.key, names: new Set(), n: 0, discord: r.discord };
    k.names.add(r.name); k.n++;
    keys.set(r.key, k);
  }
  const rows = [...keys.values()].sort((a, b) => b.n - a.n);
  const names = [...new Set(rows.map((r) => data.people[r.key] || [...r.names][0]))].sort((a, b) => a.localeCompare(b));
  put($("p-names"), names.map((n) => el("option", { value: n })));
  put($("p-rows"), rows.map((r) => el("tr", {},
    el("td", {}, el("span", { class: "p-written" }, avatar(data.people[r.key] || [...r.names][0]), [...r.names].join(" · "), r.discord ? el("span", { class: "tag" }, "Discord") : null)),
    el("td", { class: "num" }, r.n),
    el("td", {}, el("input", { class: "p-as", "data-key": r.key, list: "p-names", maxlength: "40", value: data.people[r.key] || "", placeholder: [...r.names][0], "aria-label": `Counts as, for ${[...r.names][0]}` })))));
}
$("p-save").addEventListener("click", async () => {
  const people = {};
  document.querySelectorAll(".p-as").forEach((i) => { people[i.dataset.key] = i.value.trim(); });
  try { $("p-result").textContent = (await api("/api/movies/admin", { action: "people", people })).message; await load(); }
  catch (e) { toast(e.message, true); }
});

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
for (const id of ["h-search", "h-series"]) $(id).addEventListener("input", renderHistory);
$("h-sort").addEventListener("input", () => {
  const [key, dir] = $("h-sort").value.split(":");
  if (dir) histSort = { key, dir };
  renderHistory();
});
renderColumnPicker();
$("d-movie").addEventListener("click", (e) => { if (e.target === $("d-movie")) $("d-movie").close(); });

let saved = null;
try { saved = localStorage.getItem("pingwin-movies-tab"); } catch { /* ignore */ }
switchTab(saved && $(`tab-${saved}`) && saved !== "admin" ? saved : "history");
load();
