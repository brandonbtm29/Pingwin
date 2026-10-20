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

// replaceChildren, but skipping null/false (which the DOM would print as text).
function put(node, ...kids) {
  node.replaceChildren(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false));
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
  if (res.status === 401 && state?.public) { window.location.reload(); }
  if (!res.ok) {
    const err = new Error(data.error || `Request failed (${res.status})`);
    err.stale = !!data.stale;
    if (err.stale) { presence.versions = {}; loadState().then(pingPresence); }
    throw err;
  }
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
let rolesDraft = null;   // { required_role, menus: [{ key, title, description, mode, timezone, roles: [...] }] }
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
  if (!rolesDirty) { rolesDraft = clone(state.config.roles); bases.roles = state.versions.roles; }
  if (!pcsDirty) { pcsDraft = clone(state.config.failover); bases.failover = state.versions.failover; }
  renderHeader();
  renderViewer();
  renderStatus();
  renderAbout();
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

// ---------- role menus -----------------------------------------------------
let menuIndex = 0;

const TZ_PRESET = {
  key: "timezone", title: "What's your time zone?", mode: "one", timezone: true,
  description: "Pick yours so the times you type (like game days) mean your time. Everyone sees posted times in their own zone anyway.",
  roles: [
    { name: "Eastern Time", emoji: "🌅", description: "", zone: "America/New_York" },
    { name: "Central Time", emoji: "🌾", description: "", zone: "America/Chicago" },
    { name: "Mountain Time", emoji: "⛰️", description: "", zone: "America/Denver" },
    { name: "Pacific Time", emoji: "🌊", description: "", zone: "America/Los_Angeles" },
  ],
};

function currentMenu() {
  menuIndex = Math.max(0, Math.min(menuIndex, rolesDraft.menus.length - 1));
  return rolesDraft.menus[menuIndex];
}

function roleByName(name) {
  const key = name.trim().toLowerCase();
  return state.roles.find((r) => r.name.toLowerCase() === key);
}

function roleBadge(name, pingable = true) {
  if (!name.trim()) return el("span", { class: "badge" }, "–");
  if (!state.guild) return el("span", { class: "badge" }, "offline");
  const r = roleByName(name);
  if (!r) return el("span", { class: "badge new", title: "Use “Create missing roles” after saving" }, "will be created");
  if (r.managed) return el("span", { class: "badge warn", title: "Belongs to a bot or integration" }, "can't use");
  if (!r.assignable) return el("span", { class: "badge warn", title: `Drag ${state.bot_role} above this role in Server Settings → Roles` }, "above Pingwin");
  if (pingable && !r.mentionable) return el("span", { class: "badge warn", title: "Turn on “Allow anyone to @mention this role”" }, "not pingable");
  if (r.has_permissions) return el("span", { class: "badge warn", title: "Menu roles should usually have no permissions" }, "has permissions");
  return el("span", { class: "badge ok" }, "ready");
}

function markRolesDirty() {
  rolesDirty = true;
  $("roles-dirty").hidden = false;
  $("roles-save").disabled = false;
}

function menuName(m, i) { return m.title.trim() || (i === 0 ? "Pings" : "Untitled menu"); }

function buildMenuPicker() {
  const box = $("r-menus");
  put(box,
    ...rolesDraft.menus.map((m, i) => el("button", {
      class: "menu-pill" + (i === menuIndex ? " on" : ""), role: "tab", "aria-selected": String(i === menuIndex),
      onclick: () => { menuIndex = i; buildMenuEditor(); buildMenuPicker(); },
    }, m.timezone ? "🕐 " : "", menuName(m, i), el("span", { class: "count" }, String(m.roles.length)))),
    el("button", { class: "btn", onclick: () => addMenu() }, "+ New menu"),
    rolesDraft.menus.some((m) => m.timezone) ? null : el("button", { class: "btn", onclick: () => addMenu(TZ_PRESET) }, "+ Time zone menu"),
  );
}

function addMenu(preset) {
  const m = preset ? clone(preset) : { key: "", title: "", description: "", mode: "many", timezone: false, roles: [] };
  rolesDraft.menus.push(m);
  menuIndex = rolesDraft.menus.length - 1;
  markRolesDirty();
  buildMenuPicker();
  buildMenuEditor();
  $("r-title").focus();
}

function buildMenuEditor() {
  const m = currentMenu();
  const main = menuIndex === 0;
  $("r-menu-heading").textContent = main ? "Ping menu" : menuName(m, menuIndex);
  $("r-menu-delete").hidden = main;
  $("r-title").value = m.title;
  $("r-desc").value = m.description;
  $("r-mode").value = main ? "many" : m.mode;
  $("r-mode").disabled = main;
  $("r-mode").title = main ? "The ping menu always lets people pick any number" : "";
  $("r-tz-wrap").hidden = main;
  $("r-tz").checked = !!m.timezone;
  $("r-zone-head").hidden = !m.timezone;
  buildRoleRows();
}

function renderRoles() {
  const d = rolesDraft;
  if (document.activeElement?.closest("#tab-roles")) {
    // Don't rebuild the form under the cursor during background refreshes.
    refreshRoleBadges();
  } else {
    buildMenuPicker();
    buildMenuEditor();
  }

  const gate = $("r-gate");
  const current = d.required_role;
  gate.replaceChildren(
    el("option", { value: "" }, "Everyone in the server"),
    ...state.roles.filter((r) => !r.managed).map((r) => el("option", { value: r.name }, `Only members with ${r.name}`)),
  );
  if (current && !roleByName(current)) gate.append(el("option", { value: current }, `Only members with ${current} (missing!)`));
  gate.value = current;

  $("role-names").replaceChildren(...state.roles.filter((r) => !r.managed).map((r) => el("option", { value: r.name })));

  const pm = $("r-post-menu");
  const prevMenu = pm.value;
  pm.replaceChildren(...state.config.roles.menus.map((m, i) => el("option", { value: m.key }, menuName(m, i))));
  if (prevMenu) pm.value = prevMenu;

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
      el("li", {}, el("strong", {}, m.menu_title || "A removed menu"), " in ",
        el("a", { href: m.url, target: "_blank", rel: "noreferrer" }, m.channel ? `#${m.channel}` : "a channel")))));
  }
}

function zoneSelect(row) {
  const opts = [el("option", { value: "" }, "Pick a zone…"), ...state.zones.map((z) => el("option", { value: z.zone }, z.label))];
  if (row.zone && !state.zones.some((z) => z.zone === row.zone)) opts.push(el("option", { value: row.zone }, row.zone));
  const s = el("select", { "aria-label": "Time zone", onchange: (e) => { row.zone = e.target.value; markRolesDirty(); } }, opts);
  s.value = row.zone || "";
  return s;
}

function buildRoleRows() {
  const m = currentMenu();
  const rows = m.roles;
  const tbody = $("r-rows");
  tbody.replaceChildren();
  rows.forEach((row, i) => {
    const update = (key) => (e) => { row[key] = e.target.value; markRolesDirty(); if (key === "name") refreshRoleBadges(); };
    const move = (delta) => () => {
      const j = i + delta;
      if (j < 0 || j >= rows.length) return;
      [rows[i], rows[j]] = [rows[j], rows[i]];
      markRolesDirty();
      buildRoleRows();
    };
    tbody.append(el("tr", {},
      el("td", {}, el("div", { class: "move" },
        el("button", { class: "btn icon", title: "Move up", "aria-label": "Move up", disabled: i === 0, onclick: move(-1) }, "↑"),
        el("button", { class: "btn icon", title: "Move down", "aria-label": "Move down", disabled: i === rows.length - 1, onclick: move(1) }, "↓"))),
      el("td", { class: "c-emoji" }, el("input", { value: row.emoji, maxlength: "100", "aria-label": "Emoji", oninput: update("emoji") })),
      el("td", {}, el("input", { value: row.name, list: "role-names", maxlength: "80", placeholder: "Role name", "aria-label": "Role name", oninput: update("name") })),
      el("td", {}, el("input", { value: row.description, maxlength: "200", placeholder: m.timezone ? "Optional" : "What they get pinged about", "aria-label": "Description", oninput: update("description") })),
      m.timezone ? el("td", { class: "c-zone" }, zoneSelect(row)) : null,
      el("td", { class: "c-status", "data-badge": i }, roleBadge(row.name, !m.timezone)),
      el("td", {}, el("button", { class: "btn icon", title: "Remove", "aria-label": "Remove", onclick: () => { rows.splice(i, 1); markRolesDirty(); buildRoleRows(); buildMenuPicker(); } }, "✕")),
    ));
  });
  $("r-add").disabled = rows.length >= MAX_ROLES;
  $("r-count").textContent = `${rows.length} of ${MAX_ROLES} buttons`;
}

function refreshRoleBadges() {
  const m = currentMenu();
  m.roles.forEach((row, i) => {
    const cell = document.querySelector(`[data-badge="${i}"]`);
    if (cell) cell.replaceChildren(roleBadge(row.name, !m.timezone));
  });
}

$("r-title").addEventListener("input", (e) => {
  currentMenu().title = e.target.value;
  markRolesDirty();
  $("r-menu-heading").textContent = menuIndex === 0 ? "Ping menu" : menuName(currentMenu(), menuIndex);
  buildMenuPicker();
});
$("r-desc").addEventListener("input", (e) => { currentMenu().description = e.target.value; markRolesDirty(); });
$("r-mode").addEventListener("change", (e) => { currentMenu().mode = e.target.value; markRolesDirty(); });
$("r-tz").addEventListener("change", (e) => {
  const m = currentMenu();
  m.timezone = e.target.checked;
  if (m.timezone) m.mode = "one";
  else m.roles.forEach((r) => { r.zone = ""; });
  markRolesDirty();
  buildMenuEditor();
  buildMenuPicker();
});
$("r-gate").addEventListener("change", (e) => { rolesDraft.required_role = e.target.value; markRolesDirty(); });
$("r-add").addEventListener("click", () => {
  currentMenu().roles.push({ name: "", emoji: "", description: "", zone: "" });
  markRolesDirty();
  buildRoleRows();
  buildMenuPicker();
  $("r-rows").lastElementChild?.querySelector("input[list]")?.focus();
});
$("r-menu-delete").addEventListener("click", () => {
  const m = currentMenu();
  if (!window.confirm(`Delete the menu “${menuName(m, menuIndex)}”? Its roles stay in Discord; delete posted copies of it in Discord yourself.`)) return;
  rolesDraft.menus.splice(menuIndex, 1);
  menuIndex = 0;
  markRolesDirty();
  buildMenuPicker();
  buildMenuEditor();
});

$("roles-save").addEventListener("click", async () => {
  const m = rolesDraft.menus.find((x) => x.timezone && x.roles.some((r) => r.name.trim() && !r.zone));
  if (m) return toast(`Pick a time zone for every role in “${menuName(m, 1)}”.`, true);
  $("roles-save").disabled = true;
  try {
    const res = await api("/api/roles", { ...rolesDraft, base_version: bases.roles });
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
    const res = await api("/api/action", { action: "post_menu", channel_id, menu: $("r-post-menu").value });
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
    const res = await api("/api/failover", { ...pcsDraft, base_version: bases.failover });
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

// ---------- backup PC pairing ---------------------------------------------
function friendMessage(code) {
  const ps = "& ([scriptblock]::Create((irm https://raw.githubusercontent.com/brandonbtm29/Pingwin/main/scripts/install-backup.ps1)))";
  return [
    "Hi! Here's how to set up your PC as a Pingwin backup (about 5 minutes):",
    "",
    "1. Press the Windows key, type PowerShell, and open it.",
    "2. Paste this line and press Enter:",
    "   " + ps,
    "3. When it asks, paste this invite code:",
    "   " + code,
    "4. It shows what's already installed and asks before installing anything.",
    "5. At the end it shows a reply code (already copied). Send that back to me.",
  ].join("\n");
}

$("pair-create").addEventListener("click", async () => {
  const btn = $("pair-create");
  btn.disabled = true;
  try {
    const res = await api("/api/action", { action: "pair_invite" });
    $("pair-message").value = friendMessage(res.code);
    $("pair-notes").textContent = (res.lines || []).join(" ");
    $("pair-invite").hidden = false;
  } catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; }
});

$("pair-copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("pair-message").value);
    toast("Copied. Paste it into a message to your friend.");
  } catch {
    $("pair-message").select();
    toast("Select-all is done: press Ctrl+C to copy.");
  }
});

async function loadShared() {
  const box = $("pair-shared");
  let res;
  try { res = await api("/api/action", { action: "pair_shared" }); } catch { box.replaceChildren(); return; }
  if (!res.devices.length) { box.replaceChildren(); return; }
  box.replaceChildren(
    el("h3", {}, "Sharing settings with"),
    el("ul", { class: "shared-list" }, res.devices.map((d) => el("li", {},
      el("span", { class: "name" }, d.pc || d.name || d.device_id.slice(0, 7)),
      el("code", { class: "hint", title: d.device_id }, d.device_id.slice(0, 7)),
      el("button", {
        class: "btn danger",
        onclick: async () => {
          if (!window.confirm(`Stop sharing the settings with ${d.pc || "this PC"} and take it off the PC list?`)) return;
          try {
            const r = await api("/api/action", { action: "pair_forget", device_id: d.device_id });
            const ul = $("pair-report");
            ul.replaceChildren(...r.lines.map((l) => el("li", {}, l)));
            ul.hidden = false;
            pcsDirty = false;
            await loadState();
            await loadShared();
          } catch (e) { toast(e.message, true); }
        },
      }, "Stop sharing"),
    ))),
  );
}

$("pair-accept").addEventListener("click", async () => {
  const code = $("pair-reply").value.trim();
  if (!code) return toast("Paste the reply code first.", true);
  const btn = $("pair-accept");
  btn.disabled = true;
  try {
    const res = await api("/api/action", { action: "pair_accept", code });
    const ul = $("pair-report");
    ul.replaceChildren(...res.lines.map((l) => el("li", {}, l)));
    ul.hidden = false;
    $("pair-reply").value = "";
    pcsDirty = false;
    await loadState();
    await loadShared();
  } catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; }
});

// ---------- features -------------------------------------------------------
let featureData = null;
const featureDrafts = {};
const openSections = new Set();

async function loadFeatures(force = false) {
  if (featureData && !force && Object.values(featureDrafts).some((d) => d.dirty)) return;
  try { featureData = await api("/api/features"); } catch (e) { toast(e.message, true); return; }
  $("f-restart").hidden = !featureData.restart_needed;
  renderFeatures();
  loadWatchlist();
}

function channelOptions(current) {
  const opts = [el("option", { value: "" }, "None")];
  for (const c of state?.channels || []) opts.push(el("option", { value: c.id }, `#${c.name}${c.category ? "  ·  " + c.category : ""}`));
  if (current && !(state?.channels || []).some((c) => c.id === current)) opts.push(el("option", { value: current }, `Channel ${current} (Pingwin can't post there)`));
  return opts;
}

function roleOptions(current) {
  const opts = [el("option", { value: "" }, "None")];
  for (const r of (state?.roles || []).filter((r) => !r.managed)) opts.push(el("option", { value: r.name }, r.name));
  if (current && !(state?.roles || []).some((r) => r.name.toLowerCase() === current.toLowerCase())) opts.push(el("option", { value: current }, `${current} (doesn't exist yet)`));
  return opts;
}

function fieldInput(field, value, onChange) {
  const t = field.type;
  if (t === "toggle") {
    return el("label", { class: "switch" }, el("input", { type: "checkbox", checked: !!value, onchange: (e) => onChange(e.target.checked) }), el("span", {}, field.label));
  }
  let input;
  if (t === "channel") { input = el("select", { onchange: (e) => onChange(e.target.value) }, channelOptions(value)); input.value = value || ""; }
  else if (t === "role") { input = el("select", { onchange: (e) => onChange(e.target.value) }, roleOptions(value)); input.value = value || ""; }
  else if (t === "number") input = el("input", { type: "number", min: field.min, max: field.max, value: value ?? "", oninput: (e) => onChange(e.target.value) });
  else if (t === "textarea") input = el("textarea", { rows: "3", oninput: (e) => onChange(e.target.value) }, value || "");
  else if (t === "select") { input = el("select", { onchange: (e) => onChange(e.target.value) }, field.options.map(([v, l]) => el("option", { value: v }, l))); input.value = value; }
  else if (t === "secret") input = el("input", { type: "password", autocomplete: "off", placeholder: value ? "Saved ✓ (type to replace)" : "Not set", oninput: (e) => onChange(e.target.value) });
  else if (t === "rows") return rowsInput(field, value || [], onChange);
  else input = el("input", { value: value ?? "", oninput: (e) => onChange(e.target.value) });
  return el("label", {}, field.label, input);
}

function rowsInput(field, rows, onChange) {
  const wrap = el("div", { class: "field" });
  const draw = () => {
    const body = rows.map((row, i) => el("tr", {},
      field.columns.map((col) => {
        const set = (v) => { row[col.key] = v; onChange(rows); };
        let inp;
        if (col.type === "role") { inp = el("select", { onchange: (e) => set(e.target.value) }, roleOptions(row[col.key])); inp.value = row[col.key] || ""; }
        else if (col.type === "channel") { inp = el("select", { onchange: (e) => set(e.target.value) }, channelOptions(row[col.key])); inp.value = row[col.key] || ""; }
        else inp = el("input", { value: row[col.key] || "", "aria-label": col.label, oninput: (e) => set(e.target.value) });
        return el("td", {}, inp);
      }),
      el("td", {}, el("button", { class: "btn icon", "aria-label": "Remove", onclick: () => { rows.splice(i, 1); onChange(rows); draw(); } }, "✕")),
    ));
    wrap.replaceChildren(
      el("div", { class: "label-text" }, el("strong", {}, field.label)),
      el("div", { class: "table-wrap" }, el("table", { class: "rows-table" },
        el("thead", {}, el("tr", {}, field.columns.map((c) => el("th", {}, c.label)), el("th", {}))),
        el("tbody", {}, body))),
      el("div", { class: "row-actions" }, el("button", { class: "btn", onclick: () => { rows.push(Object.fromEntries(field.columns.map((c) => [c.key, ""]))); onChange(rows); draw(); } }, "+ Add row")),
      field.help ? el("span", { class: "hint" }, field.help) : null,
    );
  };
  draw();
  return wrap;
}

function renderFeatures() {
  const box = $("f-sections");
  box.replaceChildren(...featureData.sections.map((section) => {
    const values = JSON.parse(JSON.stringify(featureData.values[section.key]));
    const draft = featureDrafts[section.key] = { values, dirty: false, base: featureData.versions?.[section.key] };
    const saveBtn = el("button", { class: "btn primary", disabled: true }, "Save");
    const dirtyNote = el("span", { class: "dirty", hidden: true }, "Unsaved changes");
    const mark = () => { draft.dirty = true; saveBtn.disabled = false; dirtyNote.hidden = false; };
    const fields = section.fields.map((field) => {
      const node = fieldInput(field, values[field.key], (v) => { values[field.key] = v; mark(); });
      return el("div", { class: "field" }, node, field.help && field.type !== "rows" ? el("span", { class: "hint" }, field.help) : null);
    });
    saveBtn.addEventListener("click", async () => {
      saveBtn.disabled = true;
      try {
        const res = await api("/api/features", { section: section.key, values, base_version: draft.base });
        draft.dirty = false;
        toast(res.message);
        await loadFeatures(true);
      } catch (e) { saveBtn.disabled = false; toast(e.message, true); }
    });
    const enabled = "enabled" in values ? values.enabled : null;
    const needs = section.needs ? el("div", { class: "needs" },
      "Needs the ", el("strong", {}, section.key === "modlog" ? "Server Members and Message Content intents" : "Server Members intent"),
      " turned on: Discord Developer Portal → your app → Bot → Privileged Gateway Intents. Restart Pingwin after saving.") : null;
    const details = el("details", { class: "feature", open: openSections.has(section.key) },
      el("summary", {}, el("h2", {}, section.title),
        enabled === null ? null : el("span", { class: enabled ? "status-on" : "status-off" }, enabled ? "On" : "Off")),
      el("div", { class: "feature-body" },
        el("p", { class: "desc" }, section.description), needs, ...fields,
        el("div", { class: "row-actions" }, saveBtn, dirtyNote)),
    );
    details.addEventListener("toggle", () => { details.open ? openSections.add(section.key) : openSections.delete(section.key); });
    return details;
  }));
}

async function loadWatchlist() {
  let data;
  try { data = await api("/api/watchlist"); } catch { return; }
  const panel = $("f-watchlist");
  panel.hidden = !data.series.length;
  $("f-watchlist-body").replaceChildren(...data.series.map((s) => el("div", { class: "watch-series" },
    el("h3", {}, s.name, s.open_poll ? el("span", { class: "tag live" }, " poll running") : null),
    s.nominations.length ? el("ul", { class: "watch-list" }, s.nominations.map((n) => el("li", { class: n.watched ? "watched" : "" },
      el("span", { class: "t" }, n.title),
      el("button", { class: "btn", onclick: () => watchAction(s.key, n.id, "watched") }, n.watched ? "Unwatch" : "Watched"),
      el("button", { class: "btn icon", "aria-label": "Remove", onclick: () => { if (window.confirm(`Remove ${n.title}?`)) watchAction(s.key, n.id, "remove"); } }, "✕"),
    ))) : el("p", { class: "hint" }, "Nothing nominated yet. Members use /movienight nominate."),
  )));
}

async function watchAction(series, id, action) {
  try { await api("/api/watchlist", { series, id, action }); await loadWatchlist(); } catch (e) { toast(e.message, true); }
}

// ---------- who's here, stale saves, sync conflicts ------------------------
const TAB_NAMES = { setup: "Setup", status: "Status", roles: "Role menu", features: "Features", pcs: "PCs & failover", logs: "Logs" };
let presence = { others: [], conflicts: 0, versions: {} };
let shownConflicts = -1;

function anyDirty() {
  return rolesDirty || pcsDirty || remoteDirty || Object.values(featureDrafts).some((d) => d.dirty);
}

function avatar(url, name) {
  return url ? el("img", { class: "avatar", src: url, alt: "", width: "22", height: "22" })
    : el("span", { class: "avatar letter", "aria-hidden": "true" }, (name || "?").trim().charAt(0).toUpperCase());
}

async function pingPresence() {
  if (!state) return;
  try {
    presence = await api("/api/presence", { tab: currentTab, dirty: anyDirty() });
  } catch { return; }
  renderPresence();
  checkStale();
  if (presence.conflicts !== shownConflicts) loadConflicts();
}

function renderPresence() {
  const bar = $("presence");
  const others = presence.others || [];
  bar.hidden = !others.length;
  if (!others.length) return;
  const sameTab = others.filter((o) => o.tab === currentTab);
  const editingHere = sameTab.filter((o) => o.dirty);
  bar.classList.toggle("busy", editingHere.length > 0);
  put(bar,
    el("span", { class: "label" }, editingHere.length ? "⚠️ Editing this tab too:" : "Also here:"),
    ...others.map((o) => el("span", { class: "who" + (o.tab === currentTab ? " here" : ""), title: `on ${o.pc}` },
      avatar(o.avatar, o.name), el("strong", {}, o.name.replace(/ \(this PC\)$/, "")),
      ` · ${TAB_NAMES[o.tab] || o.tab}`, o.dirty ? el("em", {}, " (unsaved changes)") : null)),
    editingHere.length ? el("span", { class: "hint" }, "Saving at the same time is safe: the second save is stopped instead of overwriting.") : null,
  );
}

// Which saved sections each draft is based on.
const bases = { roles: null, failover: null, dashboard: null };

function staleParts() {
  const v = presence.versions || {};
  const parts = [];
  if (rolesDirty && bases.roles && v.roles && v.roles !== bases.roles) parts.push("roles");
  if (pcsDirty && bases.failover && v.failover && v.failover !== bases.failover) parts.push("failover");
  if (remoteDirty && bases.dashboard && v.dashboard && v.dashboard !== bases.dashboard) parts.push("dashboard");
  for (const [key, d] of Object.entries(featureDrafts)) {
    if (d.dirty && d.base && v[key] && v[key] !== d.base) parts.push(key);
  }
  return parts;
}

function checkStale() {
  const parts = staleParts();
  const banner = $("stale-banner");
  banner.hidden = !parts.length;
  if (!parts.length) return;
  const edits = state?.last_edits || {};
  const lines = parts.map((k) => {
    const e = edits[k];
    const what = k === "roles" ? "the role menus" : k === "failover" ? "PCs & failover" : k === "dashboard" ? "remote access"
      : (featureData?.sections.find((s) => s.key === k)?.title || k);
    return e ? `${e.who} saved ${what} ${e.ago}` : `Someone saved ${what}`;
  });
  $("stale-text").textContent = `${lines.join("; ")} while you were editing. Saving now would undo their change, so it'll be stopped.`;
}

$("stale-reload").addEventListener("click", async () => {
  for (const k of staleParts()) {
    if (k === "roles") rolesDirty = false;
    else if (k === "failover") pcsDirty = false;
    else if (k === "dashboard") remoteDirty = false;
    else if (featureDrafts[k]) featureDrafts[k].dirty = false;
  }
  $("roles-dirty").hidden = !rolesDirty;
  $("pcs-dirty").hidden = !pcsDirty;
  document.activeElement?.blur();
  await loadState();
  if (currentTab === "features") await loadFeatures(true);
  if (currentTab === "status") await loadRemote();
  $("stale-banner").hidden = true;
  toast("Reloaded with the latest settings.");
});

async function loadConflicts() {
  let data;
  try { data = await api("/api/conflicts"); } catch { return; }
  shownConflicts = data.conflicts.length;
  const panel = $("conflicts-panel");
  panel.hidden = !data.conflicts.length;
  $("conflicts-list").replaceChildren(...data.conflicts.map((c) => el("div", { class: "conflict" },
    el("h3", {}, c.original),
    el("p", { class: "hint" }, `The other copy was saved ${c.when} (UTC) on the PC whose SyncThing ID starts with ${c.device}.`,
      c.same ? " It's identical to the current one." : ""),
    c.same ? null : el("details", {}, el("summary", {}, "What's different"), el("pre", { class: "diff" }, c.diff)),
    el("div", { class: "row-actions" },
      el("button", { class: "btn primary", onclick: () => resolveConflict(c.file, "keep_current") }, "Keep the current version"),
      c.same ? null : el("button", { class: "btn", onclick: () => { if (window.confirm(`Replace the current ${c.original} with the other copy?`)) resolveConflict(c.file, "use_other"); } }, "Use the other copy")),
  )));
}

async function resolveConflict(file, action) {
  try {
    const res = await api("/api/conflicts", { file, action });
    toast(res.message);
    await loadConflicts();
    await loadState();
  } catch (e) { toast(e.message, true); }
}

window.addEventListener("pagehide", () => {
  try {
    fetch("/api/presence", { method: "POST", keepalive: true, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Pingwin": "1" }, body: JSON.stringify({ leaving: true }) });
  } catch { /* ignore */ }
});

// ---------- who am I -------------------------------------------------------
function renderViewer() {
  const v = state.viewer;
  const box = $("viewer");
  if (!v || v.kind === "owner") { box.hidden = true; return; }
  box.hidden = false;
  box.replaceChildren(avatar(v.avatar, v.name), el("span", {}, v.name),
    el("button", { class: "btn small", onclick: async () => {
      try { await api("/api/signout", {}); } catch { /* ignore */ }
      window.location.reload();
    } }, "Sign out"));
}

// ---------- remote access --------------------------------------------------
let remote = null;
let remoteDirty = false;
let remoteDraft = null;

async function loadRemote(refresh = false) {
  try { remote = await api("/api/remote" + (refresh ? "?refresh=1" : "")); } catch (e) { $("rm-body").textContent = e.message; return; }
  if (!remoteDirty) {
    remoteDraft = { admin_role: remote.admin_role, remote_full_access: remote.remote_full_access, client_secret: "" };
    bases.dashboard = state?.versions?.dashboard || null;
  }
  renderRemote();
}

function copyButton(text, label = "Copy") {
  return el("button", { class: "btn small", onclick: async () => {
    try { await navigator.clipboard.writeText(text); toast("Copied."); } catch { toast("Couldn't copy; select it and press Ctrl+C.", true); }
  } }, label);
}

function stepItem(done, title, ...body) {
  return el("li", { class: done ? "is-done" : "" },
    el("span", { class: `mark ${done ? "done" : "todo"}` }, done ? "✓" : ""),
    el("div", { class: "step-body" }, el("strong", {}, title), ...body));
}

function renderRemote() {
  const r = remote;
  const ts = r.tailscale;
  const box = $("rm-body");
  const manage = r.can_manage;
  const online = !!ts.public_url;
  const markRemote = () => { remoteDirty = true; };

  const s1 = stepItem(ts.installed && ts.running, "Tailscale on this PC",
    !ts.installed ? el("p", {}, "Install Tailscale and sign in (free): ", el("a", { href: "https://tailscale.com/download", target: "_blank", rel: "noreferrer" }, "tailscale.com/download"), ". Only this PC needs it.")
      : !ts.running ? el("p", {}, ts.error || "Open Tailscale and sign in.")
      : !ts.funnel_allowed ? el("p", {}, "Funnel isn't allowed for this PC yet. Turn it on in the ", el("a", { href: "https://login.tailscale.com/admin/settings/features", target: "_blank", rel: "noreferrer" }, "Tailscale admin console"), ", then check again.")
      : el("p", { class: "hint" }, `Signed in as ${ts.dns_name}.`));

  const s2 = stepItem(online, "Put the dashboard online",
    online ? el("p", {}, el("a", { href: ts.public_url, target: "_blank", rel: "noreferrer", class: "mono" }, ts.public_url), " ", copyButton(ts.public_url))
      : ts.port_taken ? el("p", { class: "warn-text" }, `Port ${ts.funnel_port} on Tailscale already points at ${ts.port_taken}. Pingwin won't change that.`)
      : el("p", { class: "hint" }, `Uses Tailscale Funnel on port ${ts.funnel_port}, so anything else this PC shares on Tailscale is left alone.`),
    manage && ts.running ? el("div", { class: "row-actions" }, online
      ? el("button", { class: "btn danger", onclick: () => remoteAction({ action: "funnel_off" }, "Take the dashboard offline? Only this PC can open it then.") }, "Take it offline")
      : el("button", { class: "btn primary", disabled: !!ts.port_taken, onclick: () => remoteAction({ action: "funnel_on" }) }, "Put it online")) : null);

  const portal = r.app_id ? `https://discord.com/developers/applications/${r.app_id}/oauth2` : "https://discord.com/developers/applications";
  const secretInput = el("input", { type: "password", autocomplete: "off", placeholder: r.secret_set ? "Saved ✓ (paste to replace)" : "Paste the Client Secret", oninput: (e) => { remoteDraft.client_secret = e.target.value; markRemote(); } });
  const s3 = stepItem(r.secret_set, "Turn on Sign in with Discord",
    el("ol", { class: "sub-steps" },
      el("li", {}, "Open the ", el("a", { href: portal, target: "_blank", rel: "noreferrer" }, "Developer Portal → OAuth2"), " page for Pingwin."),
      el("li", {}, "Under ", el("strong", {}, "Redirects"), ", click Add Redirect and paste ",
        r.redirect_uri ? el("code", {}, r.redirect_uri) : "this PC's address (shown once Tailscale is on)", " ",
        r.redirect_uri ? copyButton(r.redirect_uri) : null, ". Click ", el("strong", {}, "Save Changes"), "."),
      el("li", {}, "Copy the ", el("strong", {}, "Client Secret"), " (click Reset Secret if it's hidden; that doesn't affect the bot token) and paste it here:")),
    manage ? el("div", { class: "row-actions" }, secretInput) : null);

  const roleSel = el("select", { onchange: (e) => { remoteDraft.admin_role = e.target.value; markRemote(); } },
    el("option", { value: "" }, "Nobody else"),
    ...(state?.roles || []).filter((x) => !x.managed).map((x) => el("option", { value: x.name }, x.name)));
  roleSel.value = remoteDraft.admin_role;
  const full = el("label", { class: "switch" }, el("input", { type: "checkbox", checked: remoteDraft.remote_full_access, onchange: (e) => { remoteDraft.remote_full_access = e.target.checked; markRemote(); } }),
    el("span", {}, "Also let them add or remove backup PCs and change remote access (they could then see the bot token on their own PC)"));
  const s4 = stepItem(true, "Who can sign in",
    el("p", {}, `Anyone with Manage Server in ${state?.guild?.name || "your server"}, plus members with this role:`),
    manage ? el("div", { class: "grid2" }, el("label", {}, "Extra role", roleSel)) : el("p", { class: "hint" }, r.admin_role || "No extra role."),
    manage ? full : null,
    manage ? el("div", { class: "row-actions" }, el("button", { class: "btn primary", onclick: saveRemote }, "Save")) : null);

  const s5 = stepItem(online && r.secret_set, "Share it",
    el("p", {}, "Send co-admins the link, or they can type ", el("code", {}, "/pingwin dashboard"), " in Discord. Each PC with remote access has its own link; if one is off, try the next."));

  const sessions = manage && r.sessions.length ? el("div", {},
    el("h3", {}, "Signed in from outside this PC"),
    el("ul", { class: "shared-list" }, r.sessions.map((x) => el("li", {},
      avatar(x.avatar, x.name), el("span", { class: "name" }, x.name),
      el("span", { class: "hint" }, `last seen ${ago(Math.round(Date.now() / 1000 - x.last_seen))}`),
      el("button", { class: "btn", onclick: () => remoteAction({ action: "revoke", sid: x.sid }) }, "Sign out")))),
    el("div", { class: "row-actions" }, el("button", { class: "btn danger", onclick: () => remoteAction({ action: "revoke" }, "Sign everyone out?") }, "Sign everyone out"))) : null;

  put(box,
    manage ? null : el("p", { class: "hint" }, "Only the PC's owner can change remote access."),
    el("ol", { class: "remote-steps" }, s1, s2, s3, s4, s5), sessions);
}

async function remoteAction(body, confirmText) {
  if (confirmText && !window.confirm(confirmText)) return;
  try {
    const res = await api("/api/remote", body);
    if (res.approve_url) window.open(res.approve_url, "_blank", "noreferrer");
    toast(res.message, !res.ok);
    await loadRemote(true);
  } catch (e) { toast(e.message, true); }
}

async function saveRemote() {
  try {
    const res = await api("/api/remote", { action: "save", ...remoteDraft, base_version: bases.dashboard });
    remoteDirty = false;
    toast(res.message);
    await loadState();
    await loadRemote();
  } catch (e) { toast(e.message, true); }
}

$("rm-refresh").addEventListener("click", () => loadRemote(true));

// ---------- birthday list --------------------------------------------------
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
let bdayPick = null;

function fillDays() {
  const m = Number($("b-month").value) || 1;
  const days = new Date(2024, m, 0).getDate();
  const prev = $("b-day").value;
  $("b-day").replaceChildren(...Array.from({ length: days }, (_, i) => el("option", { value: String(i + 1) }, String(i + 1))));
  if (prev && Number(prev) <= days) $("b-day").value = prev;
}
$("b-month").replaceChildren(...MONTHS.map((m, i) => el("option", { value: String(i + 1) }, m)));
$("b-month").addEventListener("change", fillDays);
fillDays();

async function loadBirthdays() {
  let data;
  try { data = await api("/api/birthdays"); } catch { return; }
  const t = $("b-table");
  if (!data.people.length) { t.replaceChildren(el("tbody", {}, el("tr", {}, el("td", { class: "hint" }, "No birthdays yet.")))); return; }
  t.replaceChildren(
    el("thead", {}, el("tr", {}, el("th", {}, "Member"), el("th", {}, "Birthday"), el("th", {}))),
    el("tbody", {}, data.people.map((p) => el("tr", {},
      el("td", {}, el("span", { class: "who" }, avatar(p.avatar, p.name), p.name)),
      el("td", {}, `${MONTHS[p.month - 1]} ${p.day}`),
      el("td", { class: "row-btns" },
        el("button", { class: "btn small", onclick: () => { pickMember({ id: p.user_id, name: p.name }); $("b-month").value = String(p.month); fillDays(); $("b-day").value = String(p.day); $("b-search").focus(); } }, "Edit"),
        el("button", { class: "btn icon", "aria-label": "Remove", onclick: () => saveBirthday({ action: "remove", user_id: p.user_id }, `Remove ${p.name}'s birthday?`) }, "✕"))))),
  );
}

function pickMember(m) {
  bdayPick = m;
  $("b-search").value = m.name;
  $("b-results").hidden = true;
}

let searchTimer = null;
$("b-search").addEventListener("input", (e) => {
  bdayPick = null;
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  if (!q) { $("b-results").hidden = true; return; }
  searchTimer = setTimeout(async () => {
    let data;
    try { data = await api("/api/members?q=" + encodeURIComponent(q)); } catch { return; }
    const ul = $("b-results");
    ul.replaceChildren(...(data.members.length ? data.members.map((m) => el("li", {},
      el("button", { type: "button", onclick: () => pickMember(m) }, avatar(m.avatar, m.name), m.name, el("span", { class: "hint" }, ` @${m.username}`))))
      : [el("li", { class: "hint" }, "Nobody found. Try the start of their name.")]));
    ul.hidden = false;
  }, 250);
});

async function saveBirthday(body, confirmText) {
  if (confirmText && !window.confirm(confirmText)) return;
  try {
    const res = await api("/api/birthdays", body);
    toast(res.message);
    await loadBirthdays();
  } catch (e) { toast(e.message, true); }
}

$("b-save").addEventListener("click", () => {
  if (!bdayPick) return toast("Pick a member from the list first.", true);
  saveBirthday({ action: "set", user_id: bdayPick.id, month: $("b-month").value, day: $("b-day").value });
  bdayPick = null;
  $("b-search").value = "";
});

// ---------- can Pingwin reach everything ----------------------------------
const ACCESS_MARK = { ok: "✓", warn: "!", bad: "✕" };
async function loadAccess() {
  let data;
  try { data = await api("/api/access"); } catch (e) { $("access-list").replaceChildren(el("li", {}, e.message)); return; }
  $("access-list").replaceChildren(...data.items.map((i) => el("li", { class: i.status },
    el("span", { class: `mark ${i.status === "ok" ? "done" : i.status === "warn" ? "warn" : "bad"}` }, ACCESS_MARK[i.status]),
    el("div", {}, el("strong", {}, i.title), i.detail ? el("div", { class: "hint" }, i.detail) : null, i.fix ? el("div", { class: "fix" }, i.fix) : null))));
}
$("access-refresh").addEventListener("click", loadAccess);

// ---------- actions --------------------------------------------------------
const CONFIRM = {
  restart: "Restart Pingwin on this PC? It's back in about 30 seconds.",
  create_roles: "Create any missing menu roles in Discord?",
  stop: "Stop Pingwin on this PC? Backup PCs take over in about 2 minutes. To start it again, use the Pingwin Dashboard shortcut on this PC.",
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
      if (action !== "restart" && action !== "stop") await loadState();
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

// ---------- setup & about --------------------------------------------------
const MARKS = { done: "✓", todo: "", warn: "!", optional: "" };

async function loadSetup() {
  let data;
  try { data = await api("/api/setup"); } catch (e) { $("setup-progress").textContent = e.message; return; }
  const { steps, required_done, required_total } = data;
  $("setup-progress").textContent = required_done === required_total
    ? "All set! 🎉"
    : `${required_done} of ${required_total} steps done`;
  $("setup-bar").style.width = `${Math.round((100 * required_done) / Math.max(1, required_total))}%`;
  $("setup-list").replaceChildren(...steps.map((s) => el("li", { class: s.status === "done" ? "is-done" : "" },
    el("span", { class: `mark ${s.status}`, "aria-label": s.status }, MARKS[s.status] ?? ""),
    el("span", { class: "step-title" }, s.title, s.required ? null : el("span", { class: "opt" }, "optional")),
    el("span", { class: "step-actions" },
      s.link && s.status !== "done" ? el("a", { class: "btn", href: s.link, target: "_blank", rel: "noreferrer" }, s.link_label || "Open") : null,
      s.tab && s.status !== "done" ? el("button", { class: "btn", onclick: () => switchTab(s.tab) }, "Go") : null),
    el("span", { class: "step-detail" }, s.detail),
  )));
}

function renderAbout() {
  $("a-version").textContent = `v${state.version}`;
  $("a-node").textContent = state.node;
  $("a-folder").textContent = state.settings_folder;
  $("a-repo").href = state.repo;
  $("a-changes").href = `${state.repo}/blob/main/CHANGELOG.md`;
  $("a-issues").href = `${state.repo}/issues`;
  $("a-privacy").href = `${state.repo}/blob/main/PRIVACY.md`;
  $("guide-link").href = `${state.repo}/blob/main/docs/SETUP.md`;
  $("a-invite").hidden = !state.invite;
  if (state.invite) $("a-invite").href = state.invite;
}

// ---------- tabs & refresh -------------------------------------------------
let currentTab = "setup";
function switchTab(name) {
  currentTab = name;
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  document.querySelectorAll(".tab").forEach((s) => { s.hidden = s.id !== `tab-${name}`; });
  if (name === "logs") loadLogs();
  if (name === "setup") loadSetup();
  if (name === "pcs") loadShared();
  if (name === "features") { loadFeatures(); loadBirthdays(); }
  if (name === "setup") loadAccess();
  if (name === "status") loadRemote();
  pingPresence();
  try { localStorage.setItem("pingwin-tab", name); } catch { /* ignore */ }
}
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));

window.addEventListener("beforeunload", (e) => {
  if (anyDirty()) e.preventDefault();
});

let savedTab = null;
try { savedTab = localStorage.getItem("pingwin-tab"); } catch { /* ignore */ }
switchTab(savedTab && document.getElementById(`tab-${savedTab}`) ? savedTab : "setup");
loadState().then(() => { pingPresence(); if (currentTab === "status") loadRemote(); });
setInterval(() => { loadState(); if (currentTab === "setup") loadSetup(); }, 10000);
setInterval(pingPresence, 15000);
setInterval(() => { if (currentTab === "logs" && $("l-follow").checked) loadLogs(); }, 5000);
