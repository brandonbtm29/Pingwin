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
  renderPending();
}

function fillSeries() {
  const keep = $("h-series").value;
  put($("h-series"), el("option", { value: "" }, "All events"), [...new Set(data.history.map((h) => h.series).filter(Boolean))].map((x) => el("option", { value: x }, evName(x))));
  $("h-series").value = keep;
  for (const id of ["w-series", "l-series"]) {
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
        el("dt", {}, "IMDb"), el("dd", {}, fmt(m.imdb_rating)),
        m.runtime ? [el("dt", {}, "Runtime"), el("dd", {}, `${m.runtime} min`)] : null,
        m.director ? [el("dt", {}, "Director"), el("dd", {}, m.director)] : null,
        m.writer ? [el("dt", {}, "Writer"), el("dd", {}, m.writer)] : null,
        m.stars ? [el("dt", {}, "Stars"), el("dd", {}, m.stars)] : null,
        m.genre ? [el("dt", {}, "Genre"), el("dd", {}, m.genre)] : null),
      m.plot ? el("p", { class: "plot" }, m.plot) : null),
    el("div", { class: "dialog-sub" }, el("h3", {}, h.ratings.length ? `Ratings (${h.ratings.length})` : "No ratings yet"),
      el("button", { class: "btn small primary", onclick: () => { d.close(); openReview(h.id); } }, "✍️ Write a review")),
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
        el("h2", {}, `${evName(w.series)} watchlist`, el("span", { class: "hint" }, ` ${waiting.length} waiting`)),
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
// Charts are plain HTML bars. Colors come from CSS roles (--c1..--c3, --pos/--neg), so dark mode
// swaps them in one place; every bar has a visible value label and a hover/focus tooltip.
function bar(pct, cls, tip) {
  const b = el("span", { class: `b ${cls}`, tabindex: "0", "data-tip": tip });
  b.style.setProperty("--w", `${Math.max(0, Math.min(100, pct))}%`);
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

  const movieLink = (x) => el("button", { class: "linkish", onclick: () => showMovie(x) }, movieLabel(x.movie));
  const avgTip = (x) => `${movieLabel(x.movie)}: ${fmt(x.avg)} from ${x.ratings.length} ratings` + (x.movie.imdb_rating ? ` · IMDb ${fmt(x.movie.imdb_rating)}` : "");
  hbars("s-top", [...rated].sort((a, b) => b.avg - a.avg).slice(0, 10), { max: 10, color: "c1", label: movieLink, value: (x) => x.avg, tip: avgTip });
  hbars("s-bottom", [...rated].sort((a, b) => a.avg - b.avg).slice(0, 10), { max: 10, color: "c2", label: movieLink, value: (x) => x.avg, tip: avgTip });

  // Diverging: us minus IMDb, centered on zero.
  const gaps = h.filter((x) => x.avg !== null && x.movie.imdb_rating).map((x) => ({ x, d: x.avg - x.movie.imdb_rating }))
    .sort((a, b) => Math.abs(b.d) - Math.abs(a.d)).slice(0, 12).sort((a, b) => b.d - a.d);
  const gMax = Math.max(1, ...gaps.map((g) => Math.abs(g.d)));
  put($("s-gap"), gaps.length ? gaps.map(({ x, d }) => {
    const b = bar((50 * Math.abs(d)) / gMax, d >= 0 ? "pos" : "neg", `${movieLabel(x.movie)}: us ${fmt(x.avg)} vs IMDb ${fmt(x.movie.imdb_rating)} (${signed(d)})`);
    const track = el("div", { class: `dt ${d >= 0 ? "up" : "down"}` }, b, el("span", { class: "hv" }, signed(d)));
    track.style.setProperty("--w", `${(50 * Math.abs(d)) / gMax}%`);
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
    label: (p) => el("span", { class: "who" }, avatar(p.name), p.name, " ", scoreBadge(p.sum / p.n)), value: (p) => p.n, tip: (p) => `${p.name}: ${p.n} reviews, average ${fmt(p.sum / p.n)}` });

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
    r.answered ? el("p", { class: "qa" }, el("em", {}, r.answered), r.answer ? ` → ${r.answer}` : "") : null,
    r.ask ? el("p", { class: "qa ask" }, "Asked the next person: ", el("em", {}, r.ask)) : null)));
  $("r-more").hidden = rows.length <= reviewLimit;
  $("r-more").textContent = `Show more (${rows.length - reviewLimit} left)`;
}
$("r-more").addEventListener("click", () => { reviewLimit += 60; renderReviews(); });
for (const id of ["r-search", "r-person", "r-series", "r-score", "r-sort"]) $(id).addEventListener("input", () => { reviewLimit = 60; renderReviews(); });

// ---------- write a review ----------
const me = () => data.viewer && data.viewer.user_id;
function reviewer() { return $("rv-who-wrap").hidden ? me() : $("rv-who").value; }
function fillReviewForm() {
  const admin = data.viewer.level === "admin";
  const keepMovie = $("rv-movie").value;
  put($("rv-movie"), data.history.map((h) => el("option", { value: h.id }, `${movieLabel(h.movie)} · ${fmtDate(h.date)}${h.series ? " · " + evName(h.series) : ""}`)));
  if (keepMovie) $("rv-movie").value = keepMovie;
  // Admins can enter a review for any member; members review as themselves.
  $("rv-who-wrap").hidden = !admin;
  if (admin) {
    const keep = $("rv-who").value || store.get("pingwin-review-as", "") || me() || "";
    put($("rv-who"), el("option", { value: "" }, "Pick a person…"), data.members.map((m) => el("option", { value: m.id }, memberLabel(m) + (m.id === me() ? " (you)" : ""))));
    $("rv-who").value = data.members.some((m) => m.id === keep) ? keep : "";
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
  const q = mine && mine.answered ? { q: mine.answered, from: "Someone" } : last ? { q: last.ask, from: last.who } : null;
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
$("rv-who").addEventListener("change", () => { store.set("pingwin-review-as", $("rv-who").value); syncReview(); });
$("rv-go").addEventListener("click", async () => {
  const who = reviewer();
  if (!who) return toast("Pick who the review is from.", true);
  const btn = $("rv-go");
  btn.disabled = true;
  try {
    const res = await api("/api/movies/review", { hid: $("rv-movie").value, as_user: who, score: $("rv-score").value,
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
function renderPeopleAdmin() {
  const keys = new Map();
  for (const h of data.history) for (const r of h.ratings) {
    const k = keys.get(r.key) || { key: r.key, names: new Set(), n: 0, discord: r.discord };
    k.names.add(r.name); k.n++;
    keys.set(r.key, k);
  }
  const rows = [...keys.values()].sort((a, b) => b.n - a.n);
  const typed = rows.map((r) => data.people[r.key]).filter((v) => v && !v.startsWith("@"));
  put($("p-names"), [...new Set(typed)].sort((a, b) => a.localeCompare(b)).map((n) => el("option", { value: n })));
  put($("p-rows"), rows.map((r) => {
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
    return el("tr", {},
      el("td", {}, el("span", { class: "p-written" }, avatar(r.names.values().next().value), [...r.names].join(" · "), own ? el("span", { class: "tag" }, "Discord") : null)),
      el("td", { class: "num" }, r.n),
      el("td", {}, sel, own ? el("div", { class: "hint" }, "Rated in Discord") : null),
      el("td", {}, name));
  }));
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
