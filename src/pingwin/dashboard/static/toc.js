// "On this page": a contents list for the open tab, and sections that fold up.
//
// Used by the dashboard and the movie pages. A section is a .panel, a feature, a group of settings (.fgroup)
// or anything marked data-toc="Title". Its title is data-toc or its first heading (a <details> panel uses its
// summary). Sections with a heading get a ▾ to fold them; each browser remembers what's folded. On wide
// screens the list sits in the empty space left of the page; otherwise a "Sections" button opens it.
// The list follows the page by itself (a MutationObserver), so pages don't need to call anything.
(() => {
  const SEL = ".panel, .feature, .fgroup, [data-toc]";
  const STORE = "pingwin-folded";
  const page = location.pathname.startsWith("/movies") ? "movies" : "dashboard";
  const main = document.querySelector("main");
  if (!main) return;

  let folded;
  try { folded = new Set(JSON.parse(localStorage.getItem(STORE) || "[]")); } catch { folded = new Set(); }
  const save = () => { try { localStorage.setItem(STORE, JSON.stringify([...folded].slice(-300))); } catch { /* fine */ } };

  const h = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v === true ? "" : v);
    }
    n.append(...kids.flat().filter((x) => x !== null && x !== undefined));
    return n;
  };
  const slug = (t) => t.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "").slice(0, 60);
  const visible = (n) => n.getClientRects().length > 0;
  const parentSection = (n) => n.parentElement && n.parentElement.closest(SEL);

  function headingOf(sec) {
    if (sec.tagName === "DETAILS") return sec.querySelector(":scope > summary");
    for (const x of sec.querySelectorAll("h2, h3")) if (x.closest(SEL) === sec) return x;
    return null;
  }
  function titleOf(sec) {
    if (sec.dataset.toc) return sec.dataset.toc;
    const head = headingOf(sec);
    if (!head) return "";
    const c = head.cloneNode(true);
    c.querySelectorAll(".hint, .n, .toc-fold, .status-on, .status-off, [aria-hidden='true']").forEach((x) => x.remove());
    return c.textContent.replace(/\s+/g, " ").trim();
  }
  // The section's own child that holds its heading: it stays visible when the section is folded.
  function headOf(sec, heading) {
    let n = heading;
    while (n && n.parentElement !== sec) n = n.parentElement;
    return n;
  }
  function keyOf(sec, tab) {
    const up = parentSection(sec);
    const own = sec.id || sec.dataset.key || slug(titleOf(sec)) || "section";
    return `${up && main.contains(up) ? keyOf(up, tab) : `${page}:${tab}`}/${own}`;
  }
  const openTab = () => main.querySelector(":scope > .tab:not([hidden])");

  // ---------- folding
  function setFolded(sec, on, remember = true) {
    if (sec.classList.contains("is-folded") !== on) sec.classList.toggle("is-folded", on);
    const btn = sec.querySelector(":scope > .toc-head .toc-fold");
    if (btn) {
      setAttr(btn, "aria-expanded", String(!on));
      setAttr(btn, "aria-label", `${on ? "Expand" : "Collapse"} ${titleOf(sec)}`);
    }
    if (remember) {
      if (on) folded.add(sec.dataset.tocKey); else folded.delete(sec.dataset.tocKey);
      save();
    }
    if (remember) schedule();
  }
  // (Only changes what isn't already so: every change in the page wakes the observer that rebuilds the list.)
  const addClass = (n, c) => { if (!n.classList.contains(c)) n.classList.add(c); };
  const setAttr = (n, k, v) => { if (n.getAttribute(k) !== v) n.setAttribute(k, v); };
  function decorate(sec, tab) {
    const key = keyOf(sec, tab);
    if (sec.dataset.tocKey !== key) sec.dataset.tocKey = key;
    if (sec.tagName === "DETAILS") return;
    const heading = headingOf(sec);
    const head = heading && headOf(sec, heading);
    if (!head) return;
    addClass(head, "toc-head");
    addClass(heading, "toc-heading");
    if (!heading.querySelector(":scope > .toc-fold")) {  // (again, if the page rewrote the heading's text)
      const btn = h("button", { type: "button", class: "toc-fold", title: "Fold or unfold this section" }, "▾");
      btn.addEventListener("click", (e) => { e.stopPropagation(); setFolded(sec, !sec.classList.contains("is-folded")); });
      heading.prepend(btn);
    }
    if (!heading.dataset.tocBound) {
      heading.dataset.tocBound = "1";
      heading.addEventListener("click", (e) => {
        if (e.target.closest("a, button, input, select, textarea, label, summary")) return;
        setFolded(sec, !sec.classList.contains("is-folded"));
      });
    }
    const first = !sec.dataset.tocDone;
    if (first) sec.dataset.tocDone = "1";
    if (first && folded.has(sec.dataset.tocKey)) setFolded(sec, true, false);
    else setFolded(sec, sec.classList.contains("is-folded"), false);  // keeps the button's label right
  }

  // ---------- the list
  const list = h("ol", { class: "toc-list" });
  const nav = h("nav", { class: "toc", "aria-label": "On this page", hidden: true },
    h("button", { type: "button", class: "toc-title", title: "Back to the top", onclick: () => { window.scrollTo({ top: 0, behavior: "smooth" }); closePop(); } }, "On this page"),
    list,
    h("div", { class: "toc-actions" },
      h("button", { type: "button", onclick: () => foldAll(false) }, "Expand all"),
      h("button", { type: "button", onclick: () => foldAll(true) }, "Collapse all")));
  const toggle = h("button", { type: "button", class: "toc-toggle", "aria-expanded": "false", hidden: true,
    onclick: () => { const open = !nav.classList.contains("open"); nav.classList.toggle("open", open); toggle.setAttribute("aria-expanded", String(open)); } },
  h("span", { "aria-hidden": "true" }, "☰"), h("span", { class: "toc-toggle-text" }, " Sections"));
  document.body.append(nav, toggle);
  function closePop() { nav.classList.remove("open"); toggle.setAttribute("aria-expanded", "false"); }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePop(); });
  document.addEventListener("click", (e) => { if (!nav.contains(e.target) && !toggle.contains(e.target)) closePop(); });

  let items = [];  // [{ sec, li }] in page order
  // Collapse all folds the top-level sections in view; Expand all opens everything on this tab.
  function foldAll(on) {
    const tab = openTab();
    for (const s of main.querySelectorAll(SEL)) {
      const t = s.closest(".tab");
      if (!s.dataset.tocDone || (t !== null && t !== tab)) continue;
      if (on && (!visible(s) || parentSection(s))) continue;
      if (s.classList.contains("is-folded") !== on) setFolded(s, on);
    }
  }
  let pinned = null;  // the section just jumped to stays highlighted until you scroll yourself
  for (const ev of ["wheel", "touchmove", "keydown", "mousedown"]) window.addEventListener(ev, (e) => { if (!nav.contains(e.target)) pinned = null; }, { passive: true });
  function jump(sec) {
    pinned = sec;
    if (sec.tagName === "DETAILS") sec.open = true;
    for (let s = sec; s && main.contains(s); s = parentSection(s)) if (s.classList.contains("is-folded")) setFolded(s, false);
    closePop();
    requestAnimationFrame(() => sec.scrollIntoView({ behavior: "smooth", block: "start" }));
  }

  function rebuild() {
    const tab = openTab();
    const tabName = tab ? tab.id.replace(/^tab-/, "") : "main";
    const found = [...main.querySelectorAll(SEL)].filter((s) => {
      if (s.closest("dialog")) return false;
      const t = s.closest(".tab");
      return (t === null || t === tab) && visible(s) && titleOf(s);
    });
    for (const s of found) decorate(s, s.closest(".tab") ? tabName : "main");
    items = [];
    const lis = new Map();
    list.replaceChildren();
    for (const s of found) {
      const up = parentSection(s);
      const parentLi = up && lis.get(up);
      const li = h("li", { class: s.classList.contains("is-folded") || (s.tagName === "DETAILS" && !s.open) ? "toc-folded" : null },
        h("button", { type: "button", onclick: () => jump(s) }, titleOf(s)));
      lis.set(s, li);
      if (parentLi) {
        let sub = parentLi.querySelector(":scope > ol");
        if (!sub) { sub = h("ol"); parentLi.append(sub); }
        sub.append(li);
      } else list.append(li);
      items.push({ sec: s, li });
    }
    const worth = items.length >= 2;
    nav.hidden = !worth;
    toggle.hidden = !worth || document.body.classList.contains("toc-wide");
    place();
  }

  // ---------- where it sits, and which section you're in
  const stickyTabs = [...document.querySelectorAll(".tabs")].find((t) => getComputedStyle(t).position === "sticky");
  function place() {
    const r = main.getBoundingClientRect();
    const room = r.left - 28;
    const wide = room >= 140;
    document.body.classList.toggle("toc-wide", wide);
    if (wide) {
      closePop();
      const width = Math.min(240, room);
      const top = Math.max(16, stickyTabs ? stickyTabs.getBoundingClientRect().bottom + 12 : 16, r.top);
      nav.style.left = `${Math.max(12, r.left - width - 20)}px`;
      nav.style.width = `${width}px`;
      nav.style.top = `${top}px`;
      nav.style.maxHeight = `calc(100vh - ${top + 16}px)`;
    } else {
      nav.style.left = nav.style.width = nav.style.top = nav.style.maxHeight = "";
    }
    toggle.hidden = nav.hidden || wide;
    const line = (stickyTabs ? stickyTabs.getBoundingClientRect().bottom : 0) + 24;
    let current = items[0];
    for (const it of items) if (it.sec.getBoundingClientRect().top <= line) current = it;
    if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4 && items.length) current = items[items.length - 1];
    current = items.find((it) => it.sec === pinned) || current;
    for (const it of items) it.li.classList.toggle("current", it === current);
  }

  let timer = null;
  function schedule() { clearTimeout(timer); timer = setTimeout(rebuild, 120); }
  let ticking = false;
  const onScroll = () => { if (!ticking) { ticking = true; requestAnimationFrame(() => { ticking = false; place(); }); } };
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll);
  new MutationObserver(schedule).observe(main, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden", "class", "open"] });
  document.addEventListener("click", (e) => { if (e.target.closest(".tabs button, .subtabs button")) schedule(); }, true);
  schedule();
})();
