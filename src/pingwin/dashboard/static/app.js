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
    const draft = featureDrafts[section.key] = { values, dirty: false };
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
        const res = await api("/api/features", { section: section.key, values });
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
  if (name === "features") loadFeatures();
  try { localStorage.setItem("pingwin-tab", name); } catch { /* ignore */ }
}
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));

window.addEventListener("beforeunload", (e) => {
  if (rolesDirty || pcsDirty || Object.values(featureDrafts).some((d) => d.dirty)) e.preventDefault();
});

let savedTab = null;
try { savedTab = localStorage.getItem("pingwin-tab"); } catch { /* ignore */ }
switchTab(savedTab && document.getElementById(`tab-${savedTab}`) ? savedTab : "setup");
loadState();
setInterval(() => { loadState(); if (currentTab === "setup") loadSetup(); }, 10000);
setInterval(() => { if (currentTab === "logs" && $("l-follow").checked) loadLogs(); }, 5000);
