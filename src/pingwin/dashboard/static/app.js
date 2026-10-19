"use strict";

// ---------- helpers --------------------------------------------------------
const $ = (id) => document.getElementById(id);
const MAX_ROLES = 25;

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

// Turn "**bold**" from report lines into safe DOM.
function richText(text) {
  const frag = document.createDocumentFragment();
  text.split("**").forEach((part, i) => frag.append(i % 2 ? el("strong", {}, part) : document.createTextNode(part)));
  return frag;
}

function toast(message, isError = false) {
  const t = el("div", { class: "toast" + (isError ? " error" : "") }, message);
  $("toasts").append(t);
  setTimeout(() => t.remove(), isError ? 9000 : 5000);
}

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Pingwin": "1" },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, { credentials: "same-origin", ...opts });
  let data = {};
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function ago(seconds) {
  if (seconds === null || seconds === undefined) return "";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  return `${Math.round(seconds / 3600)} h ago`;
}

function since(iso) {
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 3600) return `up ${Math.max(1, Math.round(s / 60))} min`;
  if (s < 86400) return `up ${Math.round(s / 3600)} h`;
  return `up ${Math.round(s / 86400)} days`;
}

// ---------- state ----------------------------------------------------------
let state = null;
let rolesDraft = null;   // { title, description, required_role, menu: [...] }
let pcsDraft = null;     // { enabled, status_channel_id, pcs, heartbeat_seconds, takeover_after_seconds }
let rolesDirty = false;
let pcsDirty = false;

const clone = (x) => JSON.parse(JSON.stringify(x));

async function loadState() {
  try {
    state = await api("/api/state");
  } catch (e) {
    setPill("offline", "Not reachable");
    $("server-line").textContent = e.message;
    return;
  }
  if (!rolesDirty) rolesDraft = clone(state.config.roles);
  if (!pcsDirty) pcsDraft = clone(state.config.failover);
  renderHeader();
  renderStatus();
  renderRoles();
  renderPcs();
}

function setPill(kind, text) {
  $("state-pill").className = "pill " + kind;
  $("state-text").textContent = text;
}

function renderHeader() {
  const g = state.guild;
  $("server-line").textContent = g ? `${g.name} · ${state.node}` : `${state.node} · not connected to a server yet`;
  if (!state.connected) setPill("offline", "Disconnected");
  else if (state.active) setPill("active", "Answering");
  else setPill("standby", "Standby");
}

// ---------- status ---------------------------------------------------------
function renderStatus() {
  const f = state.config.failover;
  $("s-node").textContent = state.node;
  $("s-node-role").textContent = f.enabled ? f.this_pc_role : "single PC";
  $("s-leader").textContent = state.leader || "–";
  $("s-checkin").textContent = state.lease_age !== null && f.enabled ? `last check-in ${ago(state.lease_age)}` : "";
  $("s-conn").textContent = state.connected ? "Connected" : "Reconnecting…";
  $("s-latency").textContent = state.latency_ms !== null ? `${state.latency_ms} ms` : "";
  $("s-version").textContent = `v${state.version}`;
  $("s-uptime").textContent = since(state.started_at);
  $("s-folder").textContent = state.settings_folder;
  $("s-persona").textContent = state.persona;
  if (state.update) {
    $("s-update").textContent = state.update.available
      ? "An update is ready. Restart Pingwin to install it."
      : "Pingwin is up to date.";
  }
  const banner = $("standby-banner");
  banner.hidden = state.active || !state.connected;
  banner.textContent = `This PC is on standby. ${state.leader || "Another PC"} is answering in Discord. You can still change settings here; every PC picks them up.`;
}

// ---------- role menu ------------------------------------------------------
function roleByName(name) {
  const key = name.trim().toLowerCase();
  return state.roles.find((r) => r.name.toLowerCase() === key);
}

function roleBadge(name) {
  if (!name.trim()) return el("span", { class: "badge" }, "–");
  if (!state.guild) return el("span", { class: "badge" }, "offline");
  const r = roleByName(name);
  if (!r) return el("span", { class: "badge new", title: "Use “Create missing roles” after saving" }, "will be created");
  if (r.managed) return el("span", { class: "badge warn", title: "Belongs to a bot or integration" }, "can't use");
  if (!r.assignable) return el("span", { class: "badge warn", title: `Drag ${state.bot_role} above this role in Server Settings → Roles` }, "above Pingwin");
  if (!r.mentionable) return el("span", { class: "badge warn", title: "Turn on “Allow anyone to @mention this role”" }, "not pingable");
  if (r.has_permissions) return el("span", { class: "badge warn", title: "Ping roles should usually have no permissions" }, "has permissions");
  return el("span", { class: "badge ok" }, "ready");
}

function markRolesDirty() {
  rolesDirty = true;
  $("roles-dirty").hidden = false;
  $("roles-save").disabled = false;
}

function renderRoles() {
  const d = rolesDraft;
  if (document.activeElement?.closest("#tab-roles")) {
    // Don't rebuild the form under the cursor during background refreshes.
    refreshRoleBadges();
  } else {
    $("r-title").value = d.title;
    $("r-desc").value = d.description;
    buildRoleRows();
  }

  // Gate role select
  const gate = $("r-gate");
  const current = d.required_role;
  gate.replaceChildren(
    el("option", { value: "" }, "Everyone in the server"),
    ...state.roles.filter((r) => !r.managed).map((r) => el("option", { value: r.name }, `Only members with ${r.name}`)),
  );
  if (current && !roleByName(current)) gate.append(el("option", { value: current }, `Only members with ${current} (missing!)`));
  gate.value = current;

  $("role-names").replaceChildren(...state.roles.filter((r) => !r.managed).map((r) => el("option", { value: r.name })));

  const sel = $("r-post-channel");
  const prev = sel.value;
  sel.replaceChildren(
    el("option", { value: "" }, "Pick a channel…"),
    ...state.channels.map((c) => el("option", { value: c.id }, `#${c.name}${c.category ? "  ·  " + c.category : ""}`)),
  );
  const rolesChannel = state.channels.find((c) => c.name.includes("roles"));
  sel.value = prev || (rolesChannel ? rolesChannel.id : "");

  const posted = $("r-posted");
  posted.replaceChildren();
  if (state.menus.length) {
    posted.append(el("h3", {}, "Posted menus"));
    posted.append(el("ul", {}, state.menus.map((m) =>
      el("li", {}, el("a", { href: m.url, target: "_blank", rel: "noreferrer" }, m.channel ? `#${m.channel}` : "a menu"), " in Discord"))));
  }
}

function buildRoleRows() {
  const tbody = $("r-rows");
  tbody.replaceChildren();
  rolesDraft.menu.forEach((row, i) => {
    const update = (key) => (e) => { row[key] = e.target.value; markRolesDirty(); if (key === "name") refreshRoleBadges(); };
    const move = (delta) => () => {
      const j = i + delta;
      if (j < 0 || j >= rolesDraft.menu.length) return;
      [rolesDraft.menu[i], rolesDraft.menu[j]] = [rolesDraft.menu[j], rolesDraft.menu[i]];
      markRolesDirty();
      buildRoleRows();
    };
    tbody.append(el("tr", {},
      el("td", {}, el("div", { class: "move" },
        el("button", { class: "btn icon", title: "Move up", "aria-label": "Move up", disabled: i === 0, onclick: move(-1) }, "↑"),
        el("button", { class: "btn icon", title: "Move down", "aria-label": "Move down", disabled: i === rolesDraft.menu.length - 1, onclick: move(1) }, "↓"))),
      el("td", { class: "c-emoji" }, el("input", { value: row.emoji, maxlength: "100", "aria-label": "Emoji", oninput: update("emoji") })),
      el("td", {}, el("input", { value: row.name, list: "role-names", maxlength: "80", placeholder: "Role name", "aria-label": "Role name", oninput: update("name") })),
      el("td", {}, el("input", { value: row.description, maxlength: "200", placeholder: "What they get pinged about", "aria-label": "Description", oninput: update("description") })),
      el("td", { class: "c-status", "data-badge": i }, roleBadge(row.name)),
      el("td", {}, el("button", { class: "btn icon", title: "Remove", "aria-label": "Remove", onclick: () => { rolesDraft.menu.splice(i, 1); markRolesDirty(); buildRoleRows(); } }, "✕")),
    ));
  });
  $("r-add").disabled = rolesDraft.menu.length >= MAX_ROLES;
  $("r-count").textContent = `${rolesDraft.menu.length} of ${MAX_ROLES} buttons`;
}

function refreshRoleBadges() {
  rolesDraft.menu.forEach((row, i) => {
    const cell = document.querySelector(`[data-badge="${i}"]`);
    if (cell) cell.replaceChildren(roleBadge(row.name));
  });
}

$("r-title").addEventListener("input", (e) => { rolesDraft.title = e.target.value; markRolesDirty(); });
$("r-desc").addEventListener("input", (e) => { rolesDraft.description = e.target.value; markRolesDirty(); });
$("r-gate").addEventListener("change", (e) => { rolesDraft.required_role = e.target.value; markRolesDirty(); });
$("r-add").addEventListener("click", () => {
  rolesDraft.menu.push({ name: "", emoji: "", description: "" });
  markRolesDirty();
  buildRoleRows();
  $("r-rows").lastElementChild?.querySelector('input[list]')?.focus();
});

$("roles-save").addEventListener("click", async () => {
  $("roles-save").disabled = true;
  try {
    const res = await api("/api/roles", rolesDraft);
    rolesDirty = false;
    $("roles-dirty").hidden = true;
    toast(res.message);
    document.activeElement?.blur();
    await loadState();
  } catch (e) {
    $("roles-save").disabled = false;
    toast(e.message, true);
  }
});

$("r-post").addEventListener("click", async () => {
  const channel_id = $("r-post-channel").value;
  if (!channel_id) return toast("Pick a channel first.", true);
  if (rolesDirty) return toast("Save your changes first.", true);
  try {
    const res = await api("/api/action", { action: "post_menu", channel_id });
    toast(res.message);
    await loadState();
  } catch (e) { toast(e.message, true); }
});

function showReport(lines) {
  const ul = $("r-report");
  ul.replaceChildren(...lines.map((l) => el("li", {}, richText(l))));
  ul.hidden = false;
}

// ---------- PCs & failover -------------------------------------------------
function markPcsDirty() {
  pcsDirty = true;
  $("pcs-dirty").hidden = false;
  $("pcs-save").disabled = false;
}

function renderPcs() {
  const f = pcsDraft;
  const live = state.config.failover;
  const lp = $("p-live");
  lp.replaceChildren();
  if (!live.enabled) {
    lp.append("Failover is off, so ", el("strong", {}, state.node), " (this PC) answers on its own.");
  } else {
    const checkin = state.lease_age !== null ? `, last check-in ${ago(state.lease_age)}` : "";
    if (state.active) {
      lp.append("This PC (", el("strong", {}, state.node), `) is answering${checkin}. Backups are standing by.`);
    } else {
      lp.append(el("strong", {}, state.leader || "Nobody yet"), ` is answering${checkin}. This PC (`,
        el("strong", {}, state.node), ") is on standby and takes over if that stops.");
    }
  }

  if (!document.activeElement?.closest("#tab-pcs")) {
    $("p-enabled").checked = f.enabled;
    $("p-hb").value = f.heartbeat_seconds;
    $("p-to").value = f.takeover_after_seconds;
  }
  const ch = $("p-channel");
  ch.replaceChildren(
    el("option", { value: "" }, "Pick a channel…"),
    ...state.channels.map((c) => el("option", { value: c.id }, `#${c.name}${c.category ? "  ·  " + c.category : ""}`)),
  );
  if (f.status_channel_id && !state.channels.some((c) => c.id === f.status_channel_id)) {
    ch.append(el("option", { value: f.status_channel_id }, `Channel ${f.status_channel_id} (can't see it)`));
  }
  ch.value = f.status_channel_id;
  buildPcList();
}

function buildPcList() {
  const ol = $("p-list");
  ol.replaceChildren();
  const me = state.node.toLowerCase();
  const leader = (state.leader || "").toLowerCase();
  pcsDraft.pcs.forEach((name, i) => {
    const move = (delta) => () => {
      const j = i + delta;
      [pcsDraft.pcs[i], pcsDraft.pcs[j]] = [pcsDraft.pcs[j], pcsDraft.pcs[i]];
      markPcsDirty();
      buildPcList();
    };
    ol.append(el("li", {},
      el("span", { class: "name" }, name),
      i === 0 ? el("span", { class: "tag" }, "main") : el("span", { class: "tag" }, `backup ${i}`),
      name.toLowerCase() === me ? el("span", { class: "tag" }, "this PC") : null,
      state.config.failover.enabled && name.toLowerCase() === leader ? el("span", { class: "tag live" }, "answering") : null,
      el("button", { class: "btn icon", "aria-label": "Move up", disabled: i === 0, onclick: move(-1) }, "↑"),
      el("button", { class: "btn icon", "aria-label": "Move down", disabled: i === pcsDraft.pcs.length - 1, onclick: move(1) }, "↓"),
      el("button", { class: "btn icon", "aria-label": "Remove", onclick: () => { pcsDraft.pcs.splice(i, 1); markPcsDirty(); buildPcList(); } }, "✕"),
    ));
  });
  if (!pcsDraft.pcs.length) ol.append(el("li", {}, el("span", { class: "name hint" }, "No PCs listed yet")));
  const listed = pcsDraft.pcs.some((p) => p.toLowerCase() === me);
  $("p-add-me").hidden = listed;
  $("p-add-me").textContent = `Add this PC (${state.node})`;
}

function addPc(name) {
  name = name.trim();
  if (!name) return;
  if (pcsDraft.pcs.some((p) => p.toLowerCase() === name.toLowerCase())) return toast(`${name} is already in the list.`, true);
  pcsDraft.pcs.push(name);
  markPcsDirty();
  buildPcList();
}

$("p-add").addEventListener("click", () => { addPc($("p-new").value); $("p-new").value = ""; });
$("p-new").addEventListener("keydown", (e) => { if (e.key === "Enter") { addPc(e.target.value); e.target.value = ""; } });
$("p-add-me").addEventListener("click", () => addPc(state.node));
$("p-enabled").addEventListener("change", (e) => { pcsDraft.enabled = e.target.checked; markPcsDirty(); });
$("p-channel").addEventListener("change", (e) => { pcsDraft.status_channel_id = e.target.value; markPcsDirty(); });
$("p-hb").addEventListener("input", (e) => { pcsDraft.heartbeat_seconds = Number(e.target.value); markPcsDirty(); });
$("p-to").addEventListener("input", (e) => { pcsDraft.takeover_after_seconds = Number(e.target.value); markPcsDirty(); });

$("pcs-save").addEventListener("click", async () => {
  if (pcsDraft.enabled && !pcsDraft.status_channel_id) return toast("Pick a status channel before turning failover on.", true);
  $("pcs-save").disabled = true;
  try {
    const res = await api("/api/failover", pcsDraft);
    pcsDirty = false;
    $("pcs-dirty").hidden = true;
    toast(res.message);
    document.activeElement?.blur();
    await loadState();
  } catch (e) {
    $("pcs-save").disabled = false;
    toast(e.message, true);
  }
});

// ---------- actions --------------------------------------------------------
const CONFIRM = {
  restart: "Restart Pingwin on this PC? It's back in about 30 seconds.",
  create_roles: "Create any missing menu roles in Discord?",
};

document.querySelectorAll("[data-action]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const action = btn.dataset.action;
    if (CONFIRM[action] && !window.confirm(CONFIRM[action])) return;
    if (action === "refresh_menus" && rolesDirty) return toast("Save your changes first.", true);
    btn.disabled = true;
    try {
      const res = await api("/api/action", { action });
      if (res.lines) { showReport(res.lines); switchTab("roles"); }
      if (res.message) toast(res.message);
      if (action !== "restart") await loadState();
    } catch (e) { toast(e.message, true); }
    finally { btn.disabled = false; }
  });
});

// ---------- logs -----------------------------------------------------------
async function loadLogs() {
  try {
    const { lines } = await api("/api/logs?lines=400");
    const warnOnly = $("l-level").value === "warn";
    const out = $("l-out");
    const atBottom = out.scrollTop + out.clientHeight >= out.scrollHeight - 20;
    out.replaceChildren(...lines
      .filter((l) => !warnOnly || /WARNING|ERROR|CRITICAL/.test(l))
      .map((l) => el("span", { class: /ERROR|CRITICAL/.test(l) ? "err" : /WARNING/.test(l) ? "warn" : "" }, l + "\n")));
    if (atBottom) out.scrollTop = out.scrollHeight;
  } catch (e) { $("l-out").textContent = e.message; }
}
$("l-level").addEventListener("change", loadLogs);

// ---------- tabs & refresh -------------------------------------------------
let currentTab = "status";
function switchTab(name) {
  currentTab = name;
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  document.querySelectorAll(".tab").forEach((s) => { s.hidden = s.id !== `tab-${name}`; });
  if (name === "logs") loadLogs();
  try { localStorage.setItem("pingwin-tab", name); } catch { /* ignore */ }
}
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));

window.addEventListener("beforeunload", (e) => { if (rolesDirty || pcsDirty) e.preventDefault(); });

try { const saved = localStorage.getItem("pingwin-tab"); if (saved) switchTab(saved); } catch { /* ignore */ }
loadState();
setInterval(loadState, 10000);
setInterval(() => { if (currentTab === "logs" && $("l-follow").checked) loadLogs(); }, 5000);
