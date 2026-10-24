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
function put(node, ...kids) { node.replaceChildren(...kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false)); }

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
const evName = (x) => (x && data && data.series_full && data.series_full[x]) || x || "";

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
  const guest = v && v.level === "viewer";
  $("m-guest").hidden = !guest;
  $("rv-box").hidden = guest;
  $("w-suggest").hidden = guest;
  document.body.classList.toggle("guest", !!guest);
  $("m-dashboard").hidden = !admin;
  $("m-admin-tab").hidden = !admin;
  $("o-note").textContent = data.omdb
    ? "Pingwin fills in genre, poster, plot, director, writers and stars by itself, a few at a time. This button does a bigger batch now."
    : "Add an OMDb API key in the dashboard (Features → Movie night) to look up details.";
  $("o-go").disabled = !data.omdb;
  const d = data.details;
  $("o-progress").hidden = !d || !d.total;
  if (d && d.total) {
    $("o-bar").style.setProperty("--pct", `${Math.round((100 * d.done) / d.total)}%`);
    $("o-status").textContent = `${d.done} of ${d.total} movies have details` + (d.not_found ? ` · ${d.not_found} not found on OMDb` : "") +
      (!data.omdb ? "." : d.paused ? ` · ${d.paused}` : d.left && d.auto ? ` · filling in ${d.left} more in the background (${d.used_today} of ${d.daily} requests used today)` : ".");
  }
  $("w-note").textContent = data.approvals && !admin ? "A mod approves suggestions before they join the list." : "";
  fillSeries();
  fillPeople();
  fillReviewForm();
  renderHistory();
  renderReviews();
  if (admin) renderPeopleAdmin();
  renderWatchlists();
  renderStats();
  renderImdb();
  if (admin) { renderImdbAdmin(); renderUnmatched(); renderBackups(); showAdminTab(store.get("pingwin-movies-admin-tab", "people")); }
  $("n-box").hidden = !admin;
  if (admin && !$("n-date").value) $("n-date").value = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  // Guests are reviewers without a Discord account (their key is their name); list them for the review form.
  const guests = new Map();
  for (const h of data.history) for (const r of h.ratings) {
    if (r.key.startsWith("name:") && !guests.has(r.key) && !(data.people[r.key] || "").startsWith("@")) guests.set(r.key, r.name);
  }
  put($("rv-guests"), [...guests.values()].sort((a, b) => a.localeCompare(b)).map((n) => el("option", { value: n })));
  renderPending();
}

function fillSeries() {
  const keep = $("h-series").value;
  put($("h-series"), el("option", { value: "" }, "All events"), [...new Set(data.history.map((h) => h.series).filter(Boolean))].map((x) => el("option", { value: x }, evName(x))));
  $("h-series").value = keep;
  for (const id of ["w-series", "l-series", "n-series"]) {
    const w = $(id).value;
    put($(id), data.series.map((x) => el("option", { value: x }, evName(x))));
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
  { key: "series", label: "Event", on: true, cell: (h) => evName(h.series), sort: (h) => nul(evName(h.series)) },
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
      el("label", {}, "Event", el("select", { id: "f-series" },
        [...new Set([...data.series, h.series].filter(Boolean))].map((x) => el("option", { value: x, selected: x === h.series }, evName(x))))),
      el("label", {}, "Title", el("input", { id: "f-title", value: m.title })),
      el("label", {}, "Year", el("input", { id: "f-year", value: m.year || "", inputmode: "numeric" })),
      el("label", { class: "span2" }, "Poster image link", el("input", { id: "f-poster", value: m.poster || "", placeholder: "https://… (right-click a poster online → Copy image address)" }))),
    el("div", { class: "row-actions" },
      el("button", { class: "btn primary", onclick: async () => {
        const body = { kind: "edit", hid: h.id };
        const pairs = [["date", h.date || ""], ["series", h.series || ""], ["title", m.title], ["year", String(m.year || "")], ["poster", m.poster || ""]];
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
        el("dt", {}, "Watched"), el("dd", {}, fmtDate(h.date)),
        el("dt", {}, "Event"), el("dd", {}, evName(h.series) || "–"),
        el("dt", {}, "Our rating"), el("dd", {}, scoreBadge(h.avg, " / 10")),
        el("dt", {}, "IMDb"), el("dd", {}, m.imdb_rating ? fmt(m.imdb_rating) : "–",
          m.imdb_id ? [" · ", imdbLink(m.imdb_id, "Open on IMDb ↗")] : el("span", { class: "hint" }, " · not matched yet"),
          admin ? [" · ", el("button", { class: "linkish", onclick: () => { d.close(); openMatcher({ kind: "movie", id: m.id, title: m.title, year: m.year }); } }, m.imdb_id ? "Wrong movie?" : "Find it")] : null),
        m.runtime ? [el("dt", {}, "Runtime"), el("dd", {}, `${m.runtime} min`)] : null,
        m.director ? [el("dt", {}, "Director"), el("dd", {}, m.director)] : null,
        m.writer ? [el("dt", {}, "Writer"), el("dd", {}, m.writer)] : null,
        m.stars ? [el("dt", {}, "Stars"), el("dd", {}, m.stars)] : null,
        m.genre ? [el("dt", {}, "Genre"), el("dd", {}, m.genre)] : null),
      m.plot ? el("p", { class: "plot" }, m.plot) : null),
    el("div", { class: "dialog-sub" }, el("h3", {}, h.ratings.length ? `Ratings (${h.ratings.length})` : "No ratings yet"),
      data.viewer.level === "viewer" ? null : el("button", { class: "btn small primary", onclick: () => { d.close(); openReview(h.id); } }, "✍️ Write a review")),
    el("ul", { class: "ratings" }, h.ratings.map((r) => el("li", {},
      el("div", { class: "r-head" }, avatar(r.who), el("strong", {}, r.who), r.who !== r.name ? el("span", { class: "hint" }, `as ${r.name}`) : null, el("span", { class: "score" }, scoreBadge(r.score)),
        admin ? el("button", { class: "btn icon", title: "Remove this rating", "aria-label": "Remove rating", onclick: async () => {
          if (!window.confirm(`Remove ${r.who}'s rating?`)) return;
          try { toast((await api("/api/movies/admin", { action: "delete_rating", hid: h.id, key: r.key })).message); d.close(); await load(); } catch (e) { toast(e.message, true); }
        } }, "✕") : null),
      r.comment ? el("p", {}, r.comment) : null,
      qaBlocks(r)))),
    data.viewer.level === "viewer" ? null : fix);
  d.showModal();
}

function imdbLink(id, label, cls = "imdb-link") {
  return el("a", { class: cls, href: `https://www.imdb.com/title/${id}/`, target: "_blank", rel: "noopener", title: "Open on IMDb" }, label);
}

// ---------- matching a movie to its IMDb entry (admins) ----------
function openMatcher(item) {
  const d = $("d-match");
  const q = el("input", { type: "search", value: item.title, "aria-label": "Search IMDb's catalog", placeholder: "Title, or paste an IMDb link" });
  const y = el("input", { value: item.year || "", placeholder: "Year", inputmode: "numeric", maxlength: "4", class: "year", "aria-label": "Year" });
  const list = el("ul", { class: "ac-list static" });
  const note = el("p", { class: "hint" });
  const find = async () => {
    put(list, el("li", { class: "ac-note" }, "Searching…"));
    try {
      const res = await api("/api/movies/admin", { action: "imdb_find", q: q.value.trim(), year: y.value.trim() });
      note.textContent = res.budget ? `${res.budget.left} of today's ${res.budget.daily} OMDb requests left.` : "";
      put(list, res.results.length ? res.results.map((h) => el("li", { class: "ac-item" },
        posterArt(h, "ac-poster"),
        el("span", { class: "ac-text" }, el("strong", {}, h.title), el("span", { class: "hint" }, String(h.year || ""))),
        imdbLink(h.imdb_id, "IMDb ↗", "ac-imdb"),
        el("button", { class: "btn small primary", onclick: async (ev) => {
          ev.target.disabled = true;
          try { toast((await api("/api/movies/admin", { action: "imdb_match", kind: item.kind, id: item.id, series_key: item.series_key || "", imdb: h.imdb_id })).message); d.close(); await load(); }
          catch (e) { toast(e.message, true); ev.target.disabled = false; }
        } }, "Use this"))) : el("li", { class: "ac-note" }, "Nothing found. Try a shorter title, no year, or paste the IMDb link."));
    } catch (e) { put(list); toast(e.message, true); }
  };
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") find(); });
  y.addEventListener("keydown", (e) => { if (e.key === "Enter") find(); });
  put(d,
    el("div", { class: "dialog-head" }, el("h2", {}, `Find ${item.title} on IMDb`), el("button", { class: "btn icon", "aria-label": "Close", onclick: () => d.close() }, "✕")),
    el("p", { class: "hint" }, "Search IMDb's catalog (through OMDb) and pick the right one, or paste the movie's IMDb link. Its poster and details replace what we had."),
    el("div", { class: "row-actions" }, q, y, el("button", { class: "btn primary", onclick: find }, "Search")),
    note, list);
  d.showModal();
  find();
}
$("d-match").addEventListener("click", (e) => { if (e.target === $("d-match")) $("d-match").close(); });
function renderUnmatched() {
  const u = data.unmatched || [];
  $("u-box").hidden = !u.length || !data.omdb;
  $("u-none").hidden = !!u.length || !data.omdb;
  $("u-title").textContent = `Not matched to IMDb (${u.length})`;
  $("a-n-details").textContent = u.length ? String(u.length) : "";
  put($("u-list"), u.slice(0, 200).map((x) => el("li", {},
    el("span", {}, el("strong", {}, x.title), x.year ? ` (${x.year})` : "",
      el("span", { class: "hint" }, x.kind === "nom" ? " · on a watchlist" : x.watched ? ` · watched ${fmtDate(x.watched)}` : " · in the library")),
    el("button", { class: "btn small", onclick: () => openMatcher(x) }, "Find it"))));
}

// The funny question chain: who asked them, and who they asked next.
const andList = (list) => list.length > 1 ? `${list.slice(0, -1).join(", ")} & ${list[list.length - 1]}` : list[0] || "";
function qaBlocks(r) {
  const out = [];
  if (r.answered) {
    out.push(el("div", { class: "qa" },
      el("span", { class: "qa-tag from" }, `From ${r.answered_from || "the last person"}`),
      el("em", {}, r.answered),
      r.answer ? el("span", { class: "qa-answer" }, `→ ${r.answer}`) : null));
  }
  if (r.ask) {
    const to = r.ask_to && r.ask_to.length ? andList(r.ask_to) : "the next person";
    out.push(el("div", { class: "qa ask" },
      el("span", { class: "qa-tag to" }, r.asked_by ? `From ${r.asked_by} · To ${to}` : `To ${to}`),
      el("em", {}, r.ask)));
  }
  return out.length ? out : null;
}

// ---------- watchlists ----------
const listFilter = {};
function renderWatchlists() {
  put($("w-lists"), data.watchlists.map((w) => {
    const waiting = w.items.filter((i) => !i.watched);
    const lists = [...new Set(waiting.map((i) => i.list || "").filter(Boolean))].sort();
    const pick = listFilter[w.series] || "";
    const shown = waiting.filter((i) => !pick || (i.list || "") === pick);
    const isFranchise = (data.franchise_series || []).includes(w.series);
    return el("div", { class: "panel" },
      isFranchise || w.queue ? queueBlock(w) : null,
      el("div", { class: "w-head" },
        el("h2", {}, isFranchise ? `${evName(w.series)}: franchises to choose from` : `${evName(w.series)} watchlist`, el("span", { class: "hint" }, ` ${waiting.length} waiting`)),
        lists.length ? el("div", { class: "chips", role: "group", "aria-label": "Lists" },
          ["", ...lists].map((l) => el("button", { class: "chip", "aria-pressed": String(pick === l), onclick: () => { listFilter[w.series] = l; renderWatchlists(); } },
            l || "All", el("span", { class: "n" }, l ? waiting.filter((i) => i.list === l).length : waiting.length)))) : null),
      shown.length ? el("ul", { class: "poster-grid" }, shown.map((i) => el("li", { class: "poster-card" },
        posterArt({ title: i.title, poster: i.poster }),
        el("div", { class: "pc-body" },
          i.imdb_id ? imdbLink(i.imdb_id, el("strong", {}, i.title), "pc-title") : el("strong", {}, i.title), i.year ? el("span", { class: "hint" }, ` ${i.year}`) : null,
          el("div", { class: "pc-meta" }, i.list ? el("span", { class: "chip small" }, i.list) : null, i.runtime ? `${i.runtime} min` : null, i.imdb_rating ? `IMDb ${fmt(i.imdb_rating)}` : null),
          i.note ? el("div", { class: "hint" }, i.note) : null,
          el("div", { class: "hint" }, `suggested by ${i.by}`))))) :
        el("p", { class: "hint" }, "Nothing waiting. Suggest one above!"));
  }));
}

// ---------- franchise queues ----------
async function queueOp(series, op, extra = {}) {
  try { const r = await api("/api/movies/admin", { action: "queue", series, op, ...extra }); if (r.message) toast(r.message); await load(); return r; }
  catch (e) { toast(e.message, true); return null; }
}

function queueBlock(w) {
  const q = w.queue;
  const admin = data.viewer.level === "admin";
  if (!q) {
    return el("div", { class: "queue empty" },
      el("h3", {}, "🎬 Franchise queue"),
      el("p", { class: "hint" }, "No franchise in progress. When a poll picks the next franchise, its queue starts here."),
      admin ? el("button", { class: "btn small primary", onclick: () => editQueue(w.series, null) }, "Start a franchise") : null);
  }
  const pct = q.total ? Math.round((100 * q.done) / q.total) : 0;
  const bar = el("div", { class: "q-bar" }, el("span", {}));
  bar.firstChild.style.setProperty("--pct", `${pct}%`);
  const status = q.finished ? "Finished! Time to poll for the next franchise." : q.paused ? "Paused: no announcements." :
    q.postponed_for ? `Not this week (${fmtDate(q.postponed_for)}).` : !q.total ? "No movies listed yet." : "";
  return el("div", { class: "queue" },
    el("div", { class: "q-head" },
      el("div", {}, el("h3", {}, `🎬 ${evName(w.series)}: ${q.name}`), el("div", { class: "hint" }, `${q.done} of ${q.total} watched`, status ? ` · ${status}` : "")),
      admin ? el("div", { class: "row-btns" },
        el("button", { class: "btn small", onclick: () => editQueue(w.series, q) }, "Edit list"),
        q.finished ? null : el("button", { class: "btn small", onclick: () => queueOp(w.series, "pause", { paused: !q.paused }) }, q.paused ? "Resume" : "Pause"),
        q.finished || q.paused ? null : el("button", { class: "btn small", onclick: () => queueOp(w.series, "postpone") }, q.postponed_for ? "Back on this week" : "Not this week"),
        el("button", { class: "btn small danger", onclick: () => { if (window.confirm(`End the ${q.name} queue?`)) queueOp(w.series, "end"); } }, "End")) : null),
    bar,
    el("ol", { class: "q-strip" }, q.items.map((i) => el("li", { class: ["q-card", i.done ? "done" : "", i.next ? "next" : "", i.skipped ? "skipped" : ""].join(" ").trim() },
      posterArt({ title: i.title, poster: i.poster }),
      el("div", { class: "q-meta" },
        el("span", { class: "q-part" }, i.skipped ? "skipped" : i.next ? `Up next · part ${i.part}` : `Part ${i.part}`),
        el("strong", {}, i.title), i.year ? el("span", { class: "hint" }, ` ${i.year}`) : null,
        i.done ? el("div", { class: "hint" }, i.done === "earlier" ? "✓ watched" : `✓ ${fmtDate(i.done)}`) : null,
        admin ? el("div", { class: "q-btns" },
          el("button", { class: "btn icon", title: "Earlier", "aria-label": "Move earlier", onclick: () => queueOp(w.series, "up", { item: i.id }) }, "↑"),
          el("button", { class: "btn icon", title: "Later", "aria-label": "Move later", onclick: () => queueOp(w.series, "down", { item: i.id }) }, "↓"),
          el("button", { class: "btn icon", title: i.done ? "Not watched" : "Mark watched", "aria-label": "Toggle watched", onclick: () => queueOp(w.series, "done", { item: i.id }) }, "✓"),
          el("button", { class: "btn icon", title: i.skipped ? "Include again" : "Skip this one", "aria-label": "Toggle skip", onclick: () => queueOp(w.series, "skip", { item: i.id }) }, "⤼")) : null)))));
}

function editQueue(series, q) {
  const d = $("d-queue");
  const name = el("input", { value: q ? q.name : "", placeholder: "e.g. Alien", maxlength: "100" });
  const text = el("textarea", { rows: "10", placeholder: "Alien (1979)\nAliens (1986)\nAlien 3 (1992)" },
    q ? q.items.filter((i) => !i.skipped).map((i) => (i.done ? "✅ " : "") + (i.year ? `${i.title} (${i.year})` : i.title)).join("\n") : "");
  put(d,
    el("div", { class: "dialog-head" }, el("h2", {}, q ? `Edit ${q.name}` : `Start a franchise for ${evName(series)}`), el("button", { class: "btn icon", "aria-label": "Close", onclick: () => d.close() }, "✕")),
    el("label", {}, "Franchise", name),
    el("label", {}, "Movies in watching order, one per line (✅ in front = already watched)", text),
    el("div", { class: "row-actions" },
      el("button", { class: "btn primary", onclick: async () => { if (await queueOp(series, "set", { name: name.value, text: text.value })) d.close(); } }, "Save"),
      el("button", { class: "btn", onclick: async (e) => {
        e.target.disabled = true;
        try { const r = await api("/api/movies/admin", { action: "queue", series, op: "suggest", name: name.value }); text.value = r.text; toast("Here's a start from OMDb. Trim it and fix the order."); }
        catch (err) { toast(err.message, true); } finally { e.target.disabled = false; }
      } }, "Suggest from OMDb"),
      el("span", { class: "hint" }, "Pingwin announces each part before showtime and logs it when the movie starts.")));
  d.showModal();
}
$("d-queue").addEventListener("click", (e) => { if (e.target === $("d-queue")) $("d-queue").close(); });

// Title suggestions while typing: movies we know first, then OMDb's catalog (IMDb's movies).
// Used by Suggest a movie and Add a movie night; each box remembers the IMDb entry picked.
function titleSuggest(input, list, yearInput, onSubmit) {
  const st = { picked: null, timer: 0, seq: 0, active: -1 };
  const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); st.active = -1; };
  const pick = (h) => { st.picked = h; input.value = h.title; yearInput.value = h.year || ""; close(); };
  const render = (res) => {
    const rows = [];
    const seen = new Set();
    const item = (h, src) => {
      const key = h.imdb_id || `${h.title}|${h.year}`;
      if (seen.has(key)) return null;
      seen.add(key);
      return el("li", { role: "option", class: "ac-item", onmousedown: (e) => { e.preventDefault(); pick(h); } },
        posterArt(h, "ac-poster"),
        el("span", { class: "ac-text" }, el("strong", {}, h.title), el("span", { class: "hint" }, [h.year, src].filter(Boolean).join(" · "))),
        h.imdb_id ? el("a", { class: "ac-imdb", href: `https://www.imdb.com/title/${h.imdb_id}/`, target: "_blank", rel: "noopener", title: "Open on IMDb", onmousedown: (e) => e.stopPropagation() }, "IMDb ↗") : null);
    };
    const lib = res.library.map((h) => item(h, h.watched ? "watched before" : "in our library")).filter(Boolean);
    const omdb = res.omdb.map((h) => item(h, "")).filter(Boolean);
    if (lib.length) rows.push(el("li", { class: "ac-head", role: "presentation" }, "Already in our library"), lib);
    if (omdb.length) rows.push(el("li", { class: "ac-head", role: "presentation" }, "From IMDb's catalog"), omdb);
    if (res.note) rows.push(el("li", { class: "ac-note", role: "presentation" }, res.note));
    if (res.warn) rows.unshift(el("li", { class: "ac-warn", role: "presentation" }, "⚠️ ", res.warn));
    put(list, rows);
    const open = rows.length > 0 && document.activeElement === input;
    list.hidden = !open;
    input.setAttribute("aria-expanded", String(open));
    st.active = -1;
  };
  input.addEventListener("input", () => {
    st.picked = null;
    clearTimeout(st.timer);
    const q = input.value.trim();
    if (q.length < 2) return close();
    st.timer = setTimeout(async () => {
      const seq = ++st.seq;
      try {
        const res = await api(`/api/movies/search?q=${encodeURIComponent(q)}`);
        if (seq === st.seq) render(res);
      } catch { /* suggestions are optional */ }
    }, 350);
  });
  input.addEventListener("keydown", (e) => {
    const items = [...list.querySelectorAll(".ac-item")];
    if (list.hidden || !items.length) { if (e.key === "Enter") onSubmit(); return; }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      st.active = (st.active + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items.forEach((li, i) => li.setAttribute("aria-selected", String(i === st.active)));
      items[st.active].scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (st.active >= 0) items[st.active].dispatchEvent(new MouseEvent("mousedown")); else { close(); onSubmit(); }
    } else if (e.key === "Escape") close();
  });
  input.addEventListener("blur", () => setTimeout(close, 120));
  return {
    imdb: () => (st.picked && st.picked.title === input.value.trim() ? st.picked.imdb_id || "" : ""),
    reset: () => { st.picked = null; input.value = ""; yearInput.value = ""; },
  };
}

const suggestBox = titleSuggest($("w-title"), $("w-ac"), $("w-year"), () => $("w-add").click());
$("w-add").addEventListener("click", async () => {
  const title = $("w-title").value.trim();
  if (!title) return toast("Type a title first.", true);
  try {
    const res = await api("/api/movies/suggest", { kind: "nominate", series: $("w-series").value, title, year: $("w-year").value.trim(), imdb_id: suggestBox.imdb() });
    toast(res.message.replace(/\*\*/g, ""));
    suggestBox.reset();
    await load();
  } catch (e) { toast(e.message, true); }
});

// Add a movie night we already watched (admins): it joins the history, then anyone can review it.
const nightBox = titleSuggest($("n-title"), $("n-ac"), $("n-year"), () => $("n-go").click());
$("n-go").addEventListener("click", async () => {
  const title = $("n-title").value.trim();
  if (!title) return toast("Type the movie's title.", true);
  if (!$("n-date").value) return toast("Pick the date you watched it.", true);
  const btn = $("n-go");
  btn.disabled = true;
  try {
    const res = await api("/api/movies/admin", { action: "add_night", title, year: $("n-year").value.trim(), imdb_id: nightBox.imdb(),
      date: $("n-date").value, series: $("n-series").value, rate: $("n-rate").checked });
    toast(res.message);
    nightBox.reset();
    await load();
    const h = data.history.find((x) => x.id === res.hid);
    if (h) showMovie(h);
  } catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; }
});

// ---------- stats ----------
// Charts are plain HTML bars. Colors come from CSS roles (--c1..--c3, --pos/--neg), so dark mode
// swaps them in one place; every bar has a visible value label and a hover/focus tooltip.
function bar(pct, cls, tip) {
  const b = el("span", { class: `b ${cls}`, tabindex: "0", "data-tip": tip });
  const p = Math.max(0, Math.min(100, pct));
  b.style.setProperty("--w", `${p}%`);
  b.style.setProperty("--r", String(p / 100));  // share of the room left after the value label
  return b;
}
function hbars(id, rows, { max, color, label, value, tip, empty = "Not enough ratings yet." }) {
  if (!rows.length) return put($(id), el("p", { class: "hint" }, empty));
  const top = max ?? Math.max(...rows.map(value), 1);
  put($(id), rows.map((r) => el("div", { class: "hrow" },
    el("div", { class: "hl" }, label(r)),
    el("div", { class: "ht" }, bar((100 * value(r)) / top, color, tip(r)), el("span", { class: "hv" }, fmt(value(r)))))));
}

function renderStats() {
  const h = data.history;
  const scored = h.flatMap((x) => x.ratings.filter((r) => typeof r.score === "number"));
  const rated = h.filter((x) => x.avg !== null && x.ratings.filter((r) => typeof r.score === "number").length >= 3);
  const minutes = h.reduce((t, x) => t + (x.movie.runtime || 0), 0);
  const groupAvg = scored.length ? scored.reduce((t, r) => t + r.score, 0) / scored.length : null;
  const tile = (cls, label, val, sub) => el("div", { class: `stat-tile ${cls}` }, el("span", { class: "k" }, label), el("strong", {}, val), sub ? el("span", { class: "s" }, sub) : null);
  put($("s-cards"),
    tile("t1", "Movie nights", h.length, h.length ? `since ${fmtDate(h.map((x) => x.date).filter(Boolean).sort()[0])}` : ""),
    tile("t2", "Hours watched", Math.round(minutes / 60).toLocaleString(), `about ${Math.round(minutes / Math.max(1, h.filter((x) => x.movie.runtime).length))} min a movie`),
    tile("t3", "Reviews written", h.reduce((t, x) => t + x.ratings.length, 0).toLocaleString(), `by ${new Set(h.flatMap((x) => x.ratings.map((r) => r.who_id || r.who))).size} critics`),
    tile("t4", "Average score", groupAvg === null ? "–" : fmt(groupAvg), "out of 10, every rating"));

  // Per year, stacked by series (the two biggest series, the rest as Other).
  const seriesCount = {};
  for (const x of h) seriesCount[x.series || "Other"] = (seriesCount[x.series || "Other"] || 0) + 1;
  const main = Object.entries(seriesCount).filter(([n]) => n !== "Other").sort((a, b) => b[1] - a[1]).slice(0, 2).map(([n]) => n);
  const groups = [...main, "Other"];
  const years = {};
  for (const x of h) {
    if (!x.date) continue;
    const y = x.date.slice(0, 4);
    const g = main.includes(x.series) ? x.series : "Other";
    years[y] = years[y] || {};
    years[y][g] = (years[y][g] || 0) + 1;
  }
  const yearRows = Object.entries(years).sort();
  const yMax = Math.max(1, ...yearRows.map(([, v]) => Object.values(v).reduce((a, b) => a + b, 0)));
  put($("s-years-legend"), groups.filter((g) => yearRows.some(([, v]) => v[g])).map((g, i) => el("span", {}, el("i", { class: `sw c${groups.indexOf(g) + 1}` }), g === "Other" ? g : evName(g))));
  put($("s-years"), yearRows.length ? yearRows.map(([y, v]) => {
    const total = Object.values(v).reduce((a, b) => a + b, 0);
    const col = el("div", { class: "stack", tabindex: "0", "data-tip": `${y}: ${total} nights · ` + groups.filter((g) => v[g]).map((g) => `${g === "Other" ? g : evName(g)} ${v[g]}`).join(" · ") });
    col.style.setProperty("--h", `${(100 * total) / yMax}%`);
    for (const g of groups) if (v[g]) { const seg = el("span", { class: `seg c${groups.indexOf(g) + 1}` }); seg.style.setProperty("--f", String(v[g])); col.append(seg); }
    return el("div", { class: "col" }, el("span", { class: "cv" }, total), el("div", { class: "track" }, col), el("span", { class: "cl" }, y));
  }) : el("p", { class: "hint" }, "No dated movie nights yet."));

  const movieLink = (x) => el("button", { class: "linkish", title: movieLabel(x.movie), onclick: () => showMovie(x) }, movieLabel(x.movie));
  const avgTip = (x) => `${movieLabel(x.movie)}: ${fmt(x.avg)} from ${x.ratings.length} ratings` + (x.movie.imdb_rating ? ` · IMDb ${fmt(x.movie.imdb_rating)}` : "");
  hbars("s-top", [...rated].sort((a, b) => b.avg - a.avg).slice(0, 10), { max: 10, color: "c1", label: movieLink, value: (x) => x.avg, tip: avgTip });
  hbars("s-bottom", [...rated].sort((a, b) => a.avg - b.avg).slice(0, 10), { max: 10, color: "c2", label: movieLink, value: (x) => x.avg, tip: avgTip });

  // Diverging: us minus IMDb, centered on zero.
  const gaps = h.filter((x) => x.avg !== null && x.movie.imdb_rating).map((x) => ({ x, d: x.avg - x.movie.imdb_rating }))
    .sort((a, b) => Math.abs(b.d) - Math.abs(a.d)).slice(0, 12).sort((a, b) => b.d - a.d);
  const gMax = Math.max(1, ...gaps.map((g) => Math.abs(g.d)));
  put($("s-gap"), gaps.length ? gaps.map(({ x, d }) => {
    const b = bar((100 * Math.abs(d)) / gMax, d >= 0 ? "pos" : "neg", `${movieLabel(x.movie)}: us ${fmt(x.avg)} vs IMDb ${fmt(x.movie.imdb_rating)} (${signed(d)})`);
    const track = el("div", { class: `dt ${d >= 0 ? "up" : "down"}` }, b, el("span", { class: "hv" }, signed(d)));
    track.style.setProperty("--r", String(Math.abs(d) / gMax));
    return el("div", { class: "drow" }, el("div", { class: "hl" }, movieLink(x)), track);
  }) : el("p", { class: "hint" }, "Needs movies with both our ratings and an IMDb rating."));

  // Histogram of every score (whole points; 9.5 counts as 9).
  const bins = Array.from({ length: 11 }, () => 0);
  for (const r of scored) bins[Math.max(0, Math.min(10, Math.floor(r.score)))]++;
  const bMax = Math.max(1, ...bins);
  const peak = bins.indexOf(bMax);
  put($("s-hist"), scored.length ? bins.map((n, i) => {
    const c = el("div", { class: "stack one", tabindex: "0", "data-tip": `${n} rating${n === 1 ? "" : "s"} of ${i}${i < 10 ? "–" + i + ".9" : ""}` });
    c.style.setProperty("--h", `${(100 * n) / bMax}%`);
    return el("div", { class: "col" }, el("span", { class: "cv" }, i === peak ? n : ""), el("div", { class: "track" }, c), el("span", { class: "cl" }, i));
  }) : el("p", { class: "hint" }, "No ratings yet."));

  const raters = {};
  for (const r of scored) { const k = r.who_id || r.who; raters[k] = raters[k] || { name: r.who, n: 0, sum: 0 }; raters[k].n++; raters[k].sum += r.score; }
  hbars("s-raters", Object.values(raters).sort((a, b) => b.n - a.n).slice(0, 10), { color: "c3",
    label: (p) => el("span", { class: "who", title: p.name }, avatar(p.name), el("span", { class: "nm" }, p.name), scoreBadge(p.sum / p.n)), value: (p) => p.n, tip: (p) => `${p.name}: ${p.n} reviews, average ${fmt(p.sum / p.n)}` });

  const genres = {};
  for (const x of h) for (const g of (x.movie.genre || "").split(",").map((t) => t.trim()).filter(Boolean)) genres[g] = (genres[g] || 0) + 1;
  hbars("s-genres", Object.entries(genres).sort((a, b) => b[1] - a[1]).slice(0, 10), { color: "c1", label: ([g]) => g, value: ([, n]) => n, tip: ([g, n]) => `${g}: ${n} movie nights`,
    empty: "Genres show up as Pingwin fills in movie details from OMDb." });

  const dirs = {};
  for (const x of h) for (const dn of (x.movie.director || "").split(",").map((t) => t.trim()).filter(Boolean)) dirs[dn] = (dirs[dn] || 0) + 1;
  hbars("s-directors", Object.entries(dirs).filter(([, n]) => n > 1).sort((a, b) => b[1] - a[1]).slice(0, 10), { color: "c2", label: ([d]) => d, value: ([, n]) => n, tip: ([d, n]) => `${d}: ${n} movie nights`,
    empty: "No director twice yet." });
}

// One tooltip for every chart mark (hover or keyboard focus).
(() => {
  const tip = $("tip");
  const show = (e) => {
    const t = e.target.closest && e.target.closest("[data-tip]");
    if (!t) { tip.hidden = true; return; }
    tip.textContent = t.dataset.tip;
    tip.hidden = false;
    const r = t.getBoundingClientRect();
    const w = tip.offsetWidth;
    tip.style.setProperty("left", `${Math.max(8, Math.min(window.innerWidth - w - 8, r.left + r.width / 2 - w / 2))}px`);
    tip.style.setProperty("top", `${Math.max(8, r.top - tip.offsetHeight - 8) + window.scrollY}px`);
  };
  document.addEventListener("mouseover", show);
  document.addEventListener("focusin", show);
  document.addEventListener("mouseout", (e) => { if (e.target.closest && e.target.closest("[data-tip]")) tip.hidden = true; });
  document.addEventListener("focusout", () => { tip.hidden = true; });
})();

// ---------- reviews ----------
const personKey = (r) => (r.who_id ? `id:${r.who_id}` : `n:${(r.who || "").trim().toLowerCase()}`);
let reviewLimit = 60;

function allReviews() {
  const out = [];
  for (const h of data.history) for (const r of h.ratings) out.push({ ...r, h });
  return out;
}

function peopleSummary(reviews) {
  const people = new Map();
  for (const r of reviews) {
    const k = personKey(r);
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
  put($("r-series"), el("option", { value: "" }, "All events"), [...new Set(data.history.map((h) => h.series).filter(Boolean))].map((x) => el("option", { value: x }, evName(x))));
  $("r-series").value = sk;
}

function renderReviews() {
  const q = $("r-search").value.trim().toLowerCase();
  const who = $("r-person").value;
  const series = $("r-series").value;
  const band = $("r-score").value;
  const inBand = (sc) => band === "" || (typeof sc === "number" && (band === "8" ? sc >= 8 : band === "5" ? sc >= 5 && sc < 8 : sc < 5));
  const everyone = allReviews();
  let rows = everyone.filter((r) => (!who || personKey(r) === who) && (!series || r.h.series === series) && inBand(r.score)
    && (!q || [r.comment, r.answer, r.ask, r.answered, r.who, r.name, r.answered_from, ...(r.ask_to || []), r.h.movie.title, r.h.movie.year].join(" ").toLowerCase().includes(q)));
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
    const mine = everyone.filter((r) => personKey(r) === who && typeof r.score === "number");
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
    qaBlocks(r))));
  $("r-more").hidden = rows.length <= reviewLimit;
  $("r-more").textContent = `Show more (${rows.length - reviewLimit} left)`;
}
$("r-more").addEventListener("click", () => { reviewLimit += 60; renderReviews(); });
for (const id of ["r-search", "r-person", "r-series", "r-score", "r-sort"]) $(id).addEventListener("input", () => { reviewLimit = 60; renderReviews(); });

// ---------- write a review ----------
const me = () => data.viewer && data.viewer.user_id;
const GUEST = "__guest";
const guestName = () => $("rv-guest").value.replace(/\s+/g, " ").trim();
function reviewer() {
  if ($("rv-who-wrap").hidden) return me();
  if ($("rv-who").value === GUEST) return guestName() ? `name:${guestName().toLowerCase()}` : "";
  return $("rv-who").value;
}
function fillReviewForm() {
  const admin = data.viewer.level === "admin";
  const keepMovie = $("rv-movie").value;
  put($("rv-movie"), data.history.map((h) => el("option", { value: h.id }, `${movieLabel(h.movie)} · ${fmtDate(h.date)}${h.series ? " · " + evName(h.series) : ""}`)));
  if (keepMovie) $("rv-movie").value = keepMovie;
  // Admins can enter a review for any member; members review as themselves.
  $("rv-who-wrap").hidden = !admin;
  if (admin) {
    const keep = $("rv-who").value || store.get("pingwin-review-as", "") || me() || "";
    put($("rv-who"), el("option", { value: "" }, "Pick a person…"),
      el("option", { value: GUEST }, "➕ A guest without Discord…"),
      el("optgroup", { label: "Members" }, data.members.map((m) => el("option", { value: m.id }, memberLabel(m) + (m.id === me() ? " (you)" : "")))));
    $("rv-who").value = keep === GUEST || data.members.some((m) => m.id === keep) ? keep : "";
    $("rv-guest-wrap").hidden = $("rv-who").value !== GUEST;
  }
  $("rv-as-note").textContent = !admin && data.viewer.name ? ` as ${data.viewer.name}` : "";
  syncReview();
}
function syncReview() {
  const h = data.history.find((x) => x.id === $("rv-movie").value);
  const who = reviewer();
  const mine = h && who ? h.ratings.find((r) => r.key === who) : null;
  $("rv-go").textContent = mine ? "Update review" : "Post review";
  if (mine) {
    $("rv-score").value = typeof mine.score === "number" ? mine.score : 7;
    $("rv-comment").value = mine.comment || "";
    $("rv-answer").value = mine.answer || "";
    $("rv-ask").value = mine.ask || "";
  } else {
    $("rv-comment").value = ""; $("rv-answer").value = ""; $("rv-ask").value = "";
  }
  // The question the last reviewer passed on (or the one this person already answered).
  const others = h ? h.ratings.filter((r) => r.key !== who) : [];
  const last = [...others].reverse().find((r) => r.ask);
  const q = mine && mine.answered ? { q: mine.answered, from: mine.answered_from || "Someone" } : last ? { q: last.ask, from: last.who } : null;
  $("rv-q-wrap").hidden = !q;
  if (q) { $("rv-q").textContent = q.q; $("rv-q-from").textContent = q.from; }
  put($("rv-score-badge"), scoreBadge(Number($("rv-score").value), " / 10"));
}
function openReview(hid) {
  switchTab("reviews");
  $("rv-box").open = true;
  if (hid) $("rv-movie").value = hid;
  syncReview();
  $("rv-box").scrollIntoView({ behavior: "smooth", block: "start" });
}
$("rv-score").addEventListener("input", () => put($("rv-score-badge"), scoreBadge(Number($("rv-score").value), " / 10")));
$("rv-movie").addEventListener("change", syncReview);
$("rv-who").addEventListener("change", () => {
  store.set("pingwin-review-as", $("rv-who").value);
  $("rv-guest-wrap").hidden = $("rv-who").value !== GUEST;
  if (!$("rv-guest-wrap").hidden) $("rv-guest").focus();
  syncReview();
});
$("rv-guest").addEventListener("input", syncReview);
$("rv-go").addEventListener("click", async () => {
  const who = reviewer();
  const isGuest = !$("rv-who-wrap").hidden && $("rv-who").value === GUEST;
  if (!who) return toast(isGuest ? "Type the guest's name." : "Pick who the review is from.", true);
  const btn = $("rv-go");
  btn.disabled = true;
  try {
    const res = await api("/api/movies/review", { hid: $("rv-movie").value, ...(isGuest ? { guest: guestName() } : { as_user: who }), score: $("rv-score").value,
      comment: $("rv-comment").value, answer: $("rv-q-wrap").hidden ? "" : $("rv-answer").value, ask: $("rv-ask").value });
    $("rv-result").textContent = res.message;
    await load();
  } catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; }
});

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
function memberLabel(m) { return m.username && m.username !== m.name ? `${m.name} (@${m.username})` : m.name; }
function likelyMembers(names) {
  const words = [...names].flatMap((n) => String(n).toLowerCase().split(/[^\p{L}\p{N}]+/u)).filter((w) => w.length >= 3 && !["mrs", "mr", "the", "and"].includes(w));
  if (!words.length) return [];
  return data.members.filter((m) => {
    const mine = [m.name, m.username, m.global].join(" ").toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean);
    return words.some((w) => mine.some((x) => x.startsWith(w) || w.startsWith(x) && x.length >= 4));
  });
}
// Combining: tick several names, then pick who they all are.
const pPicked = new Set();
let pRows = [];
function syncPicked() {
  for (const k of [...pPicked]) if (!pRows.some((r) => r.key === k)) pPicked.delete(k);
  $("p-bar").hidden = !pPicked.size;
  $("p-picked").textContent = `${pPicked.size} name${pPicked.size === 1 ? "" : "s"} picked`;
  document.querySelectorAll("#p-rows tr.p-row").forEach((tr) => tr.classList.toggle("picked", pPicked.has(tr.dataset.key)));
}
$("p-clear").addEventListener("click", () => { pPicked.clear(); document.querySelectorAll("#p-rows .pick input").forEach((b) => { b.checked = false; }); syncPicked(); });
$("p-combine").addEventListener("click", () => {
  const rows = pRows.filter((r) => pPicked.has(r.key));
  if (rows.length < 2) return toast("Tick at least two names to combine.", true);
  const d = $("d-combine");
  const owned = rows.filter((r) => /^\d+$/.test(r.key)).map((r) => r.key);
  const linked = rows.map((r) => (data.people[r.key] || "")).filter((v) => v.startsWith("@")).map((v) => v.slice(1));
  const suggest = owned[0] || linked[0] || "";
  const typed = rows.map((r) => data.people[r.key]).find((v) => v && !v.startsWith("@")) || "";
  const sel = el("select", { "aria-label": "Discord member" }, el("option", { value: "" }, "— no Discord account —"),
    data.members.map((m) => el("option", { value: m.id, selected: m.id === suggest }, memberLabel(m))));
  const name = el("input", { maxlength: "40", list: "p-names", value: suggest ? "" : typed || [...rows[0].names][0], placeholder: "Their name", "aria-label": "Name" });
  const sync = () => { name.disabled = !!sel.value; nameWrap.classList.toggle("muted", !!sel.value); };
  const nameWrap = el("label", {}, "Or, for someone without Discord, a name", name);
  sel.addEventListener("change", sync);
  put(d,
    el("div", { class: "dialog-head" }, el("h2", {}, `Combine ${rows.length} names`), el("button", { class: "btn icon", "aria-label": "Close", onclick: () => d.close() }, "✕")),
    el("p", { class: "hint" }, "All of these ratings will count as one person:"),
    el("ul", { class: "combine-list" }, rows.map((r) => el("li", {}, el("strong", {}, [...r.names].join(" · ")), el("span", { class: "hint" }, ` · ${r.n} rating${r.n === 1 ? "" : "s"}`), /^\d+$/.test(r.key) ? el("span", { class: "tag" }, "Discord") : null))),
    el("label", {}, "Count them as this Discord member", sel),
    nameWrap,
    owned.length > 1 ? el("p", { class: "hint warn" }, "More than one Discord account is ticked. They'll all count as the member you pick (like someone's old and new account).") : null,
    el("div", { class: "row-actions" },
      el("button", { class: "btn primary", onclick: async () => {
        const as = sel.value ? `@${sel.value}` : name.value.replace(/\s+/g, " ").trim();
        if (!as) return toast("Pick a member or type a name.", true);
        const people = {};
        for (const r of rows) people[r.key] = /^\d+$/.test(r.key) && as === `@${r.key}` ? "" : as;
        try {
          toast((await api("/api/movies/admin", { action: "people", people })).message);
          pPicked.clear(); d.close(); await load();
        } catch (e) { toast(e.message, true); }
      } }, "Combine"),
      el("button", { class: "btn", onclick: () => d.close() }, "Cancel")));
  sync();
  d.showModal();
});
$("d-combine").addEventListener("click", (e) => { if (e.target === $("d-combine")) $("d-combine").close(); });

// Admin sub-tabs
function showAdminTab(name) {
  document.querySelectorAll("#a-tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.sub === name)));
  document.querySelectorAll(".a-sub").forEach((s) => { s.hidden = s.dataset.sub !== name; });
  store.set("pingwin-movies-admin-tab", name);
}
document.querySelectorAll("#a-tabs button").forEach((b) => b.addEventListener("click", () => showAdminTab(b.dataset.sub)));
function renderBackups() {
  const b = data.backups || [];
  put($("b-list"), b.length ? b.map((x) => el("li", {}, el("strong", {}, fmtDate(x.day)), el("span", { class: "hint" }, ` · ${x.file} · ${Math.max(1, Math.round(x.bytes / 1024)).toLocaleString()} KB`)))
    : el("li", { class: "hint" }, "The backup is made with the next change to the movie pages."));
}

// Who a rater key counts as right now: a member ("@id"), a typed name, or nobody yet.
function personOf(key) {
  const v = data.people[key] || "";
  if (/^\d+$/.test(key)) return v.startsWith("@") ? v : `@${key}`;
  return v.startsWith("@") ? v : v ? `name:${v.toLowerCase()}` : "";
}
function renderPeopleAdmin() {
  const keys = new Map();
  for (const h of data.history) for (const r of h.ratings) {
    const k = keys.get(r.key) || { key: r.key, names: new Set(), n: 0, discord: r.discord, who: r.who };
    k.names.add(r.name); k.n++;
    keys.set(r.key, k);
  }
  const rows = [...keys.values()].sort((a, b) => b.n - a.n);
  pRows = rows;
  const typed = rows.map((r) => data.people[r.key]).filter((v) => v && !v.startsWith("@"));
  put($("p-names"), [...new Set(typed)].sort((a, b) => a.localeCompare(b)).map((n) => el("option", { value: n })));
  // Group the names that count as the same person; the rest wait at the bottom.
  const groups = new Map();
  const loose = [];
  for (const r of rows) {
    const g = personOf(r.key);
    if (!g) { loose.push(r); continue; }
    if (!groups.has(g)) groups.set(g, { id: g, rows: [], n: 0 });
    const grp = groups.get(g);
    grp.rows.push(r); grp.n += r.n;
  }
  const memberName = (id) => { const m = data.members.find((x) => x.id === id); return m ? memberLabel(m) : null; };
  const title = (g) => g.id.startsWith("@") ? (memberName(g.id.slice(1)) || g.rows[0].who || "A former member") : g.rows[0].who || data.people[g.rows[0].key];
  const sorted = [...groups.values()].sort((a, b) => b.n - a.n || title(a).localeCompare(title(b)));
  const head = (label, sub, n, cls) => el("tr", { class: `p-group ${cls || ""}` },
    el("th", { colspan: "5" }, el("span", { class: "p-written" }, cls === "loose" ? null : avatar(label), el("strong", {}, label), el("span", { class: "hint" }, sub)),
      el("span", { class: "p-total" }, `${n} rating${n === 1 ? "" : "s"}`)));
  $("p-summary").textContent = `${sorted.length} ${sorted.length === 1 ? "person" : "people"} grouped from ${rows.length - loose.length} names` + (loose.length ? ` · ${loose.length} not linked yet` : "");
  put($("p-rows"),
    sorted.map((g) => [
      head(title(g), g.id.startsWith("@") ? `Discord member · ${g.rows.length} name${g.rows.length === 1 ? "" : "s"}` : `${g.rows.length} name${g.rows.length === 1 ? "" : "s"}`, g.n),
      g.rows.map(personRow)]),
    loose.length ? [head("Not linked yet", "pick a member or type a name to group these", loose.reduce((t, r) => t + r.n, 0), "loose"), loose.map(personRow)] : null);
  $("a-n-people").textContent = loose.length ? String(loose.length) : "";
  syncPicked();
}
function personRow(r) {
  const own = /^\d+$/.test(r.key);  // rated in Discord: already that member
  const cur = data.people[r.key] || "";
  const linked = own ? r.key : cur.startsWith("@") ? cur.slice(1) : "";
  const likely = own ? [] : likelyMembers(r.names);
  const opt = (m) => el("option", { value: m.id, selected: m.id === linked }, memberLabel(m));
  const sel = el("select", { class: "p-member", "data-key": r.key, disabled: own, "aria-label": `Discord member for ${[...r.names][0]}` },
    el("option", { value: "" }, own ? "" : "— not linked —"),
    likely.length ? el("optgroup", { label: "Likely" }, likely.map(opt)) : null,
    el("optgroup", { label: "Everyone" }, data.members.filter((m) => !likely.includes(m)).map(opt)),
    linked && !data.members.some((m) => m.id === linked) ? el("option", { value: linked, selected: true }, "A former member") : null);
  const name = el("input", { class: "p-as", "data-key": r.key, list: "p-names", maxlength: "40", value: cur.startsWith("@") ? "" : cur,
    placeholder: [...r.names][0], disabled: own || !!linked, "aria-label": `Name for ${[...r.names][0]}` });
  sel.addEventListener("change", () => { name.disabled = !!sel.value; if (sel.value) name.value = ""; });
  const pick = el("input", { type: "checkbox", checked: pPicked.has(r.key), "aria-label": `Pick ${[...r.names][0]} to combine` });
  pick.addEventListener("change", () => { if (pick.checked) pPicked.add(r.key); else pPicked.delete(r.key); syncPicked(); });
  return el("tr", { class: "p-row" + (pPicked.has(r.key) ? " picked" : ""), "data-key": r.key },
    el("td", { class: "pick" }, pick),
    el("td", {}, el("span", { class: "p-written" }, [...r.names].join(" · "), own ? el("span", { class: "tag" }, "Discord") : null)),
    el("td", { class: "num" }, r.n),
    el("td", {}, sel, own ? el("div", { class: "hint" }, "Rated in Discord") : null),
    el("td", {}, name));
}
$("p-save").addEventListener("click", async () => {
  const people = {};
  document.querySelectorAll(".p-member").forEach((sel) => {
    if (sel.disabled) return;
    const typed = document.querySelector(`.p-as[data-key="${CSS.escape(sel.dataset.key)}"]`);
    people[sel.dataset.key] = sel.value ? `@${sel.value}` : (typed ? typed.value.trim() : "");
  });
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

// ---------- IMDb lists ----------
let xList = store.get("pingwin-imdb-list", "");
let xShow = "all", xLimit = 80;
const X_FILTERS = [["all", "All"], ["watched", "Watched"], ["listed", "On a watchlist"], ["neither", "Not yet"]];
function xStatus(i) { return i.watched.length ? "watched" : i.lists.length ? "listed" : "neither"; }
function renderImdb() {
  const lists = data.imdb_lists || [];
  $("m-imdb-tab").hidden = !lists.length;
  if (!lists.length) return;
  const cur = lists.find((l) => l.id === xList) || lists[0];
  xList = cur.id;
  put($("x-lists"), lists.length > 1 ? lists.map((l) => el("button", { class: "chip", "aria-pressed": String(l.id === cur.id),
    onclick: () => { xList = l.id; store.set("pingwin-imdb-list", l.id); xLimit = 80; renderImdb(); } }, l.name, el("span", { class: "n" }, l.counts.total))) : null);
  put($("x-meta"), el("strong", {}, cur.name), ` · ${cur.counts.total} movies · updated ${fmtDate(new Date(cur.imported * 1000).toISOString().slice(0, 10))}`,
    cur.url ? [" · ", el("a", { href: cur.url, target: "_blank", rel: "noopener" }, "Open on IMDb ↗")] : null);
  const c = cur.counts;
  const pct = (n) => (c.total ? Math.round((100 * n) / c.total) : 0);
  const tile = (t, label, n) => el("div", { class: `stat-tile ${t}` }, el("span", { class: "k" }, label), el("strong", {}, n.toLocaleString()),
    el("span", { class: "s" }, `${pct(n)}% of the list`), el("div", { class: "x-meter" }, el("span", { style: `--w:${pct(n)}%` })));
  put($("x-counts"), tile("t3", "Watched at movie night", c.watched), tile("t1", "On a watchlist", c.listed), tile("t2", "Not yet", c.neither));
  const counts = { all: c.total, watched: c.watched, listed: c.listed, neither: c.neither };
  put($("x-filter"), X_FILTERS.map(([k, label]) => el("button", { class: "chip", "aria-pressed": String(xShow === k),
    onclick: () => { xShow = k; xLimit = 80; renderImdb(); } }, label, el("span", { class: "n" }, counts[k]))));
  const q = $("x-search").value.trim().toLowerCase();
  const sort = $("x-sort").value;
  const rows = cur.items.filter((i) => (xShow === "all" || xStatus(i) === xShow) && (!q || `${i.title} ${i.year || ""}`.toLowerCase().includes(q)));
  const by = {
    position: (a, b) => (a.position || 1e9) - (b.position || 1e9),
    added: (a, b) => String(b.added || "").localeCompare(String(a.added || "")),
    title: (a, b) => a.title.localeCompare(b.title),
    year: (a, b) => (b.year || 0) - (a.year || 0),
    mine: (a, b) => (b.your_rating ?? -1) - (a.your_rating ?? -1),
  };
  rows.sort(by[sort] || by.position);
  const guest = data.viewer.level === "viewer";
  put($("x-rows"), rows.slice(0, xLimit).map((i) => {
    const st = xStatus(i);
    const status = st === "watched"
      ? i.watched.map((w) => el("button", { class: "x-tag watched", onclick: () => { const h = data.history.find((x) => x.id === w.hid); if (h) showMovie(h); } },
          `✅ ${fmtDate(w.date)}`, w.series ? ` · ${evName(w.series)}` : "", w.avg !== null ? [" · ", scoreBadge(w.avg)] : null))
      : st === "listed" ? i.lists.map((s) => el("span", { class: "x-tag listed" }, `📋 On the ${evName(s)} watchlist`))
      : [el("span", { class: "x-tag neither" }, "Not yet"), guest ? null : addToWatchlist(i)];
    return el("li", { class: `x-row ${st}` },
      posterArt(i, "x-poster"),
      el("div", { class: "x-main" },
        el("div", { class: "x-title" }, i.position ? el("span", { class: "x-pos" }, `#${i.position}`) : null,
          i.imdb_id ? el("a", { href: `https://www.imdb.com/title/${i.imdb_id}/`, target: "_blank", rel: "noopener" }, i.title) : el("span", {}, i.title),
          i.year ? el("span", { class: "hint" }, ` (${i.year})`) : null),
        el("div", { class: "hint" }, [i.added ? `added ${fmtDate(i.added)}` : null, typeof i.your_rating === "number" ? `your rating ${fmt(i.your_rating)}/10` : null].filter(Boolean).join(" · ")),
        el("div", { class: "x-status" }, status)));
  }));
  $("x-more").hidden = rows.length <= xLimit;
  $("x-more").textContent = `Show more (${rows.length - xLimit} left)`;
  $("x-missing-box").hidden = !cur.missing.length;
  $("x-missing-title").textContent = `Watched with us, not on this list (${cur.missing.length})`;
  put($("x-missing"), cur.missing.map((m) => el("li", {}, el("strong", {}, m.title), m.year ? ` (${m.year})` : "", el("span", { class: "hint" }, ` · ${fmtDate(m.date)}${m.series ? " · " + evName(m.series) : ""}`))));
}
function addToWatchlist(i) {
  const sel = el("select", { class: "x-series", "aria-label": `Watchlist for ${i.title}` }, data.series.map((s) => el("option", { value: s }, evName(s))));
  const go = el("button", { class: "btn small", onclick: async () => {
    go.disabled = true;
    try {
      const res = await api("/api/movies/suggest", { kind: "nominate", series: sel.value, title: i.title, year: i.year ? String(i.year) : "", imdb_id: i.imdb_id || "" });
      toast(res.message.replace(/\*\*/g, ""));
      await load();
    } catch (e) { toast(e.message, true); go.disabled = false; }
  } }, "＋ Add");
  return el("span", { class: "x-add" }, sel, go);
}
$("x-search").addEventListener("input", () => { xLimit = 80; renderImdb(); });
$("x-sort").addEventListener("input", renderImdb);
$("x-more").addEventListener("click", () => { xLimit += 80; renderImdb(); });
function renderImdbAdmin() {
  put($("x-admin"), (data.imdb_lists || []).map((l) => el("li", {},
    el("span", {}, el("strong", {}, l.name), ` · ${l.counts.total} movies · ${l.counts.watched} watched with us`),
    el("span", { class: "row-btns" },
      el("button", { class: "btn small", onclick: () => { $("x-name").value = l.name; $("x-url").value = l.url || ""; $("x-file").click(); } }, "Replace…"),
      el("button", { class: "btn small danger", onclick: async () => {
        if (!window.confirm(`Remove ${l.name}? (Your list on IMDb isn't touched.)`)) return;
        try { toast((await api("/api/movies/admin", { action: "imdb_list", op: "delete", id: l.id })).message); await load(); } catch (e) { toast(e.message, true); }
      } }, "Remove")))));
}
$("x-file").addEventListener("change", () => {
  const f = $("x-file").files[0];
  if (f && !$("x-name").value.trim()) $("x-name").value = f.name.replace(/\.csv$/i, "").replace(/[_-]+/g, " ").trim();
});
$("x-go").addEventListener("click", async () => {
  const btn = $("x-go");
  const csv = await readFile($("x-file"));
  if (!csv) return toast("Pick the CSV file you exported from IMDb.", true);
  btn.disabled = true;
  $("x-result").textContent = "Reading the list…";
  try {
    const res = await api("/api/movies/admin", { action: "imdb_list", op: "preview", csv });
    $("x-result").textContent = "";
    reviewList(csv, res.rows);
  } catch (e) { $("x-result").textContent = ""; toast(e.message, true); }
  finally { btn.disabled = false; }
});

// Review an IMDb export before it's saved: untick anything that shouldn't be kept.
function reviewList(csv, rows) {
  const d = $("d-review");
  const keep = new Set(rows.map((r) => r.row));
  let show = "all";
  const status = (r) => (r.watched.length ? "watched" : r.lists.length ? "listed" : "neither");
  const count = el("span", { class: "hint" });
  const body = el("tbody");
  const name = el("input", { value: $("x-name").value.trim(), placeholder: "Name, like Horror log", maxlength: "60", "aria-label": "List name" });
  const url = el("input", { value: $("x-url").value.trim(), placeholder: "IMDb list link (optional)", maxlength: "220", "aria-label": "IMDb list link" });
  const draw = () => {
    const shown = rows.filter((r) => show === "all" || status(r) === show);
    put(body, shown.map((r) => {
      const box = el("input", { type: "checkbox", checked: keep.has(r.row), "aria-label": `Keep ${r.title}` });
      box.addEventListener("change", () => { if (box.checked) keep.add(r.row); else keep.delete(r.row); sync(); });
      const st = status(r);
      return el("tr", { class: keep.has(r.row) ? "" : "dropped" },
        el("td", {}, box),
        el("td", {}, r.imdb_id ? imdbLink(r.imdb_id, r.title) : r.title, r.year ? el("span", { class: "hint" }, ` (${r.year})`) : null),
        el("td", {}, st === "watched" ? el("span", { class: "x-tag watched" }, `✅ watched ${fmtDate(r.watched[0].date)}`)
          : st === "listed" ? el("span", { class: "x-tag listed" }, `📋 ${r.lists.map(evName).join(", ")}`)
          : el("span", { class: "x-tag neither" }, "Not yet")),
        el("td", { class: "hint" }, r.match ? `${r.match.title}${r.match.year ? ` (${r.match.year})` : ""}` : "–",
          r.fills.length ? el("div", {}, `fills in ${r.fills.map((f) => f.replace("imdb_id", "IMDb link").replace("imdb_rating", "IMDb rating")).join(", ")}`) : null));
    }));
  };
  const sync = () => {
    count.textContent = `${keep.size} of ${rows.length} movies will be kept.`;
    save.disabled = !keep.size;
    save.textContent = `Save ${keep.size} movie${keep.size === 1 ? "" : "s"}`;
    draw();
  };
  const filters = el("div", { class: "chips" }, [["all", "All"], ["watched", "Watched"], ["listed", "On a watchlist"], ["neither", "Not yet"]].map(([k, label]) =>
    el("button", { class: "chip", "aria-pressed": String(k === show), onclick: (ev) => {
      show = k; ev.currentTarget.parentNode.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c === ev.currentTarget))); draw();
    } }, label, el("span", { class: "n" }, k === "all" ? rows.length : rows.filter((r) => status(r) === k).length))));
  const visible = () => rows.filter((r) => show === "all" || status(r) === show);
  const save = el("button", { class: "btn primary", onclick: async () => {
    save.disabled = true;
    try {
      const skip = rows.filter((r) => !keep.has(r.row)).map((r) => r.row);
      const res = await api("/api/movies/admin", { action: "imdb_list", csv, skip, name: name.value.trim(), url: url.value.trim() });
      toast(res.message); $("x-result").textContent = res.message;
      $("x-file").value = ""; $("x-name").value = ""; $("x-url").value = "";
      d.close(); await load();
    } catch (e) { toast(e.message, true); save.disabled = false; }
  } });
  put(d,
    el("div", { class: "dialog-head" }, el("h2", {}, "Review the IMDb list"), el("button", { class: "btn icon", "aria-label": "Close", onclick: () => d.close() }, "✕")),
    el("p", { class: "hint" }, "Nothing is saved until you press Save. Untick movies you don't want in this list. Movies that match one in our library also fill in what it's missing."),
    el("div", { class: "row-actions" }, name, url),
    el("div", { class: "row-actions" }, filters,
      el("button", { class: "btn small", onclick: () => { visible().forEach((r) => keep.add(r.row)); sync(); } }, "Tick shown"),
      el("button", { class: "btn small", onclick: () => { visible().forEach((r) => keep.delete(r.row)); sync(); } }, "Untick shown")),
    el("div", { class: "table-wrap review-table" }, el("table", { class: "movie-table" },
      el("thead", {}, el("tr", {}, el("th", {}, "Keep"), el("th", {}, "On the IMDb list"), el("th", {}, "For us"), el("th", {}, "Matches in our library"))), body)),
    el("div", { class: "row-actions" }, save, count));
  sync();
  d.showModal();
}
$("d-review").addEventListener("click", (e) => { if (e.target === $("d-review")) $("d-review").close(); });
$("u-retry").addEventListener("click", async () => {
  const btn = $("u-retry");
  btn.disabled = true; $("u-result").textContent = "Looking them up again…";
  try { $("u-result").textContent = (await api("/api/movies/admin", { action: "imdb_retry" })).message; await load(); }
  catch (e) { $("u-result").textContent = ""; toast(e.message, true); }
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
