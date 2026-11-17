
import { get, send } from "./api.js";
import { chartSelect, disposeChart, drawChart } from "./charts.js";
import { icon } from "./icons.js";
import { renderTable } from "./table.js";
import { onThemeChange } from "./theme.js";
import { $, compact, copyText, debounce, el, full, highlightSql, nf, pretty, toast } from "./util.js";

const STATE_KEY = "ipdr.explore.v2";
let meta = null;
let cfg = null;
let viewEl = null;
let chart = null;
let lastRun = null;
let lastCount = null;
let sideOpen = false;

const IDENTITY = ["subscriber_id", "client_ip", "server_ip", "imsi", "imei"];
const EXAMPLES = ["0325-3047295", "tiktok karachi", "jazz", "adult sites isb", "facebook.com"];


function pad(n) { return String(n).padStart(2, "0"); }
function toLocalInput(d) { return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`; }
function parseWall(s) { const [d, t = "00:00"] = s.replace("T", " ").split(" "); const [y, m, day] = d.split("-").map(Number); const [hh, mm] = t.split(":").map(Number); return new Date(Date.UTC(y, m - 1, day, hh || 0, mm || 0)); }

const PRESETS = [["hour", "Last hour"], ["day", "Last 24 h"], ["fullday", "Last full day"], ["week", "Last 7 days"],
  ["month", "Last 30 days"], ["all", "All data"]];

function presetRange(name) {
  const r = meta.data_range || {};
  const last = r.last ? parseWall(r.last) : new Date();
  const lastFull = r.last_full_day ? parseWall(r.last_full_day) : last;
  const end = new Date(last.getTime() + 60000);
  const minus = (d, h) => new Date(d.getTime() - h * 3600000);
  switch (name) {
    case "hour": return [minus(end, 1), end];
    case "day": return [minus(end, 24), end];
    case "fullday": return [lastFull, new Date(lastFull.getTime() + 86400000)];
    case "week": return [new Date(lastFull.getTime() - 6 * 86400000), new Date(lastFull.getTime() + 86400000)];
    case "month": return [new Date(lastFull.getTime() - 29 * 86400000), new Date(lastFull.getTime() + 86400000)];
    case "all": return [r.first ? parseWall(r.first.slice(0, 10)) : minus(end, 24 * 365), end];
    default: return [minus(end, 1), end];
  }
}

function activePreset() {
  return (PRESETS.find(([key]) => {
    const [s, e] = presetRange(key);
    return toLocalInput(s) === cfg.start && toLocalInput(e) === cfg.end;
  }) || [null])[0];
}

function setRange(s, e) { cfg.start = toLocalInput(s); cfg.end = toLocalInput(e); }

function defaultCfg() {
  const [s, e] = presetRange("hour");
  return {
    start: toLocalInput(s), end: toLocalInput(e), filters: [], mode: "records",
    columns: [...meta.default_columns], sort: "start_time", desc: true, limit: 100, offset: 0,
    group_by: ["app_label"], measures: ["connections", "users", "volume_gb"],
    identified_only: false, hide_background: false, lookup: "", lookupNote: "",
  };
}

const queryKey = (c) => { const { offset, lookup, lookupNote, ...rest } = c; return JSON.stringify(rest); };
const countKey = (c) => JSON.stringify([c.start, c.end, c.filters, !!c.identified_only, !!c.hide_background]);

function saveState() {
  try { sessionStorage.setItem(STATE_KEY, JSON.stringify(cfg)); } catch {  }
  markStale();
}
function loadState() {
  try { const s = JSON.parse(sessionStorage.getItem(STATE_KEY) || "null"); return s && s.start ? s : null; } catch { return null; }
}

function markStale() {
  if (!viewEl) return;
  const stale = !!lastRun && (lastRun.mode !== cfg.mode || queryKey(lastRun.snap) !== queryKey(cfg));
  $("#explore-results", viewEl)?.classList.toggle("stale", stale);
  $("#ex-run", viewEl)?.classList.toggle("attention", stale);
  const s = $("#explore-status", viewEl);
  if (s && (stale || s.dataset.stale)) {
    s.textContent = stale ? "Changed. Run to update." : "";
    s.className = "status-line";
    if (stale) s.dataset.stale = "1"; else delete s.dataset.stale;
  }
}

const runSoon = debounce(() => run(0), 350);
function changed(now = false) {
  cfg.offset = 0;
  saveState();
  if (now) runSoon();
}

const fieldByName = (n) => meta.fields.find((f) => f.name === n);
const short = (v, n = 36) => { const s = String(v); return s.length > n ? `${s.slice(0, n - 1)}…` : s; };


function filterCard(f, i) {
  const fieldSel = el("select", { class: "input", "aria-label": "Field" },
    meta.fields.filter((x) => x.kind !== "time").map((x) => el("option", { value: x.name, text: x.kind === "group" ? "app group (kind of app)" : x.name })));
  fieldSel.value = f.field;
  const kind = (fieldByName(f.field) || {}).kind || "string";
  const opSel = el("select", { class: "input", "aria-label": "Condition" }, (meta.ops[kind] || []).map((o) => el("option", { value: o.op, text: o.label })));
  if (![...opSel.options].some((o) => o.value === f.op)) f.op = opSel.options[0].value;
  opSel.value = f.op;
  const listId = `vals-${i}-${Math.random().toString(36).slice(2, 7)}`;
  const groupOptions = (fieldByName(f.field) || {}).options || [];
  const value = kind === "group"
    ? el("select", { class: "input", "aria-label": "App group" },
        el("option", { value: "", text: "Choose a group…" }),
        groupOptions.map((o) => el("option", { value: o.value, text: o.label, title: o.description })))
    : el("input", { class: "input", value: f.value || "", list: kind === "string" ? listId : null, "aria-label": "Value",
        placeholder: placeholderFor(f.field, f.op, kind) });
  if (kind === "group") {
    value.value = f.value || "";
    value.addEventListener("change", () => { f.value = value.value; cfg.lookupNote = ""; changed(!!f.value); });
  }
  const datalist = el("datalist", { id: listId });
  value.hidden = f.op === "empty" || f.op === "not_empty";
  const remove = el("button", { type: "button", class: "icon-btn", title: "Remove this filter", "aria-label": "Remove this filter", html: icon("trash", "sm") });

  fieldSel.addEventListener("change", () => { f.field = fieldSel.value; f.op = ""; f.value = ""; cfg.lookupNote = ""; saveState(); drawSide(); });
  opSel.addEventListener("change", () => {
    f.op = opSel.value;
    saveState(); drawSide();
    if (f.op === "empty" || f.op === "not_empty") changed(true);
  });
  value.addEventListener("input", () => { f.value = value.value; cfg.lookupNote = ""; saveState(); suggest(); });
  value.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); run(0); } });
  remove.addEventListener("click", () => { cfg.filters.splice(i, 1); cfg.lookupNote = ""; drawSide(); changed(true); });

  const suggest = debounce(async () => {
    if (kind !== "string" || !["eq", "ne", "in", "starts", "contains"].includes(f.op)) return;
    const term = f.op === "in" ? value.value.split(",").pop().trim() : value.value.trim();
    try {
      const out = await get(`/api/explore/values?field=${encodeURIComponent(f.field)}&q=${encodeURIComponent(term)}`);
      datalist.replaceChildren(...out.values.map((v) => el("option", { value: v.value, label: `${nf.format(v.rows)} records` })));
    } catch {  }
  }, 250);
  value.addEventListener("focus", suggest);

  return el("div", { class: "ex-filter" },
    el("div", { class: "ex-filter-top" }, fieldSel, remove),
    el("div", { class: "ex-filter-bottom" }, opSel, value, datalist));
}

function placeholderFor(field, op, kind) {
  if (op === "in") return kind === "number" ? "e.g. 80, 443" : "values, separated by commas";
  if (op === "between") return "e.g. 1000, 50000";
  if (field === "subscriber_id") return "e.g. 0325-3047295";
  if (kind === "number") return "number";
  return "value";
}

function checkMenu(label, items, isOn, onToggle, extras = null) {
  const wrap = el("div", { class: "menu-wrap" });
  const btn = el("button", { type: "button", class: "btn btn-sm", "aria-haspopup": "true", "aria-expanded": "false" });
  const setLabel = () => { btn.innerHTML = `<span>${label()}</span>${icon("chevron", "sm")}`; };
  setLabel();
  const search = items.length > 12 ? el("input", { type: "search", class: "input", placeholder: "Find…", "aria-label": "Find" }) : null;
  const list = el("div", { class: "picker-list" });
  const draw = () => {
    const q = search ? search.value.trim().toLowerCase() : "";
    list.replaceChildren(...items.filter((it) => !q || it.key.includes(q) || (it.title || "").toLowerCase().includes(q)).map((it) => {
      const cb = el("input", { type: "checkbox" });
      cb.checked = isOn(it.key);
      cb.addEventListener("change", () => { onToggle(it.key, cb.checked); setLabel(); });
      return el("label", { class: "picker-item", title: it.title || "" }, cb,
        el("span", { class: it.mono ? "mono" : "", text: it.text }), it.note ? el("span", { class: "muted picker-type", text: it.note }) : null);
    }));
  };
  search?.addEventListener("input", draw);
  const panel = el("div", { class: "menu picker", hidden: true }, search, extras ? extras(() => { setLabel(); draw(); }) : null, list);
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    panel.hidden = !panel.hidden;
    btn.setAttribute("aria-expanded", String(!panel.hidden));
    if (!panel.hidden) { draw(); (search || list.querySelector("input"))?.focus(); }
  });
  panel.addEventListener("click", (e) => e.stopPropagation());
  const close = () => {
    if (!panel.isConnected) { document.removeEventListener("click", close); return; }
    panel.hidden = true;
    btn.setAttribute("aria-expanded", "false");
  };
  document.addEventListener("click", close);
  wrap.append(btn, panel);
  return wrap;
}


async function lookup(text) {
  text = (text || "").trim();
  if (!text) return;
  cfg.lookup = text;
  let out;
  try {
    out = await get(`/api/explore/interpret?q=${encodeURIComponent(text)}`);
  } catch (e) {
    toast(e.message);
    return;
  }
  if (!out.filters.length) {
    cfg.lookupNote = `Could not tell what “${short(text, 60)}” is. Type a phone number, IP address, website, app, kind of app, operator or city, or add a filter on the left.`;
    saveState(); drawTop();
    return;
  }
  cfg.filters = out.filters;
  cfg.mode = "records";
  cfg.offset = 0;
  let widened = false;
  if (out.filters.some((f) => IDENTITY.includes(f.field)) && parseWall(cfg.end) - parseWall(cfg.start) < 6 * 86400000) {
    const [s, e] = presetRange("week");
    setRange(s, e);
    widened = true;
  }
  cfg.lookupNote = `Showing ${out.understood.join(" · ")}${widened ? ", over the last 7 days" : ""}.`
    + (out.unknown.length ? ` Not used: ${out.unknown.join(", ")}.` : "");
  saveState();
  draw();
  run(0);
}


export async function renderExplore(view) {
  viewEl = view;
  if (!meta) {
    view.replaceChildren(el("div", { class: "page-inner" }, el("p", { class: "muted", text: "Loading…" })));
    try {
      meta = await get("/api/explore/fields");
    } catch (e) {
      view.replaceChildren(el("div", { class: "page-inner" }, el("p", { class: "callout bad", text: e.message })));
      return;
    }
  }
  cfg = { ...defaultCfg(), ...(cfg || loadState() || {}) };
  draw();
  if (!lastRun) run(0);
}

function draw() {
  closeMenu();
  viewEl.replaceChildren(el("div", { class: "page-inner wide explore" },
    el("div", { class: "ex-top", id: "ex-top" }),
    el("div", { class: "ex-layout" },
      el("aside", { class: `ex-side panel${sideOpen ? " open" : ""}`, id: "ex-side", "aria-label": "Search" }),
      el("section", { class: "ex-main", "aria-label": "Results" },
        el("div", { class: "ex-toolbar panel", id: "ex-toolbar" }),
        el("section", { class: "panel explore-results", id: "explore-results" })))));
  drawTop();
  drawSide();
  drawToolbar();
  if (lastRun && lastRun.mode === cfg.mode) showResult(lastRun.data, lastRun.snap);
  else emptyState();
  markStale();
}

function drawTop() {
  const box = $("#ex-top", viewEl);
  if (!box) return;
  const look = el("input", { type: "search", class: "input lookup-input", value: cfg.lookup || "", "aria-label": "Quick look-up",
    placeholder: "Look up a phone number, IP, website, app, operator or city" });
  const lookBtn = el("button", { type: "button", class: "btn btn-primary", onclick: () => lookup(look.value) });
  lookBtn.innerHTML = `<span>Look up</span>`;
  look.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); lookup(look.value); } });

  const viewsSel = el("select", { class: "input", "aria-label": "Saved views" }, el("option", { value: "", text: "Saved views" }));
  const saveBtn = el("button", { type: "button", class: "btn btn-sm", text: "Save view", onclick: saveView });
  const delBtn = el("button", { type: "button", class: "icon-btn", title: "Delete the selected view", "aria-label": "Delete the selected view", html: icon("trash", "sm") });
  delBtn.disabled = true;
  get("/api/views").then((list) => {
    viewsSel.append(...list.map((v) => { const o = el("option", { value: v.id, text: v.name }); o._cfg = v.config; return o; }));
  }).catch(() => {});
  viewsSel.addEventListener("change", () => {
    const o = viewsSel.selectedOptions[0];
    delBtn.disabled = !viewsSel.value;
    if (o && o._cfg) { cfg = { ...defaultCfg(), ...o._cfg, offset: 0, lookupNote: "" }; saveState(); draw(); run(0); }
  });
  delBtn.addEventListener("click", async () => {
    if (!viewsSel.value || !confirm("Delete this saved view?")) return;
    try { await send("DELETE", `/api/views/${viewsSel.value}`); toast("View deleted"); drawTop(); } catch (e) { toast(e.message); }
  });

  const note = cfg.lookupNote
    ? el("p", { class: `lookup-note${cfg.filters.length ? "" : " bad"}`, text: cfg.lookupNote })
    : el("p", { class: "lookup-note muted" }, "Try ", ...EXAMPLES.flatMap((x, i) => [
        i ? ", " : "", el("button", { type: "button", class: "link", text: x, onclick: () => { look.value = x; lookup(x); } })]),
      ". No AI involved: what you set here is exactly what runs.");

  box.replaceChildren(
    el("div", { class: "ex-top-row" },
      el("h1", { class: "ex-title", text: "Explore" }),
      el("div", { class: "lookup-line" }, look, lookBtn),
      el("div", { class: "ex-views" }, viewsSel, delBtn, saveBtn)),
    note);
}

function drawSide() {
  const side = $("#ex-side", viewEl);
  if (!side) return;
  const current = activePreset();
  const presets = el("div", { class: "ex-presets" }, PRESETS.map(([k, t]) => el("button", {
    type: "button", class: `btn btn-sm${current === k ? " active" : ""}`, text: t, "aria-pressed": String(current === k),
    onclick: () => { const [s, e] = presetRange(k); setRange(s, e); drawSide(); changed(true); },
  })));
  const startIn = el("input", { type: "datetime-local", class: "input", value: cfg.start, "aria-label": "From" });
  const endIn = el("input", { type: "datetime-local", class: "input", value: cfg.end, "aria-label": "To" });
  startIn.addEventListener("change", () => { cfg.start = startIn.value; drawSide(); changed(true); });
  endIn.addEventListener("change", () => { cfg.end = endIn.value; drawSide(); changed(true); });

  const addFilter = el("button", { type: "button", class: "btn btn-sm btn-ghost", title: "Add a filter" });
  addFilter.innerHTML = `${icon("plus", "sm")}<span>Add</span>`;
  addFilter.addEventListener("click", () => {
    cfg.filters.push({ field: "app_label", op: "eq", value: "" });
    saveState(); drawSide();
    [...side.querySelectorAll(".ex-filter")].pop()?.querySelector(".ex-filter-bottom .input:last-of-type")?.focus();
  });
  const filters = cfg.filters.length
    ? el("div", { class: "ex-filters" }, cfg.filters.map((f, i) => filterCard(f, i)),
        el("button", { type: "button", class: "link ex-clear", text: "Clear all filters",
          onclick: () => { cfg.filters = []; cfg.lookup = ""; cfg.lookupNote = ""; drawTop(); drawSide(); changed(true); } }))
    : el("p", { class: "muted ex-none", text: "None. Every record in the time range is included." });

  const toggle = (key, text, title) => {
    const cb = el("input", { type: "checkbox" });
    cb.checked = !!cfg[key];
    cb.addEventListener("change", () => { cfg[key] = cb.checked; changed(true); });
    return el("label", { class: "ex-check", title }, cb, el("span", { text }));
  };

  const runBtn = el("button", { type: "button", class: "btn btn-primary ex-run-btn", id: "ex-run", onclick: () => run(0) });
  runBtn.innerHTML = `${icon("play", "sm")}<span>Run</span>`;
  const reset = el("button", { type: "button", class: "link", text: "Reset", onclick: () => { cfg = defaultCfg(); saveState(); draw(); run(0); } });

  const fold = el("button", { type: "button", class: "ex-fold", "aria-expanded": String(sideOpen),
    onclick: () => { sideOpen = !sideOpen; side.classList.toggle("open", sideOpen); fold.setAttribute("aria-expanded", String(sideOpen)); } },
    el("span", { class: "ex-fold-label", text: "Search" }), el("span", { class: "ex-fold-sum", text: searchSummary().join(" · ") }),
    el("span", { html: icon("chevron", "sm") }));

  side.replaceChildren(fold, el("div", { class: "ex-side-body" },
    el("section", { class: "ex-block" },
      el("div", { class: "ex-h" }, el("h2", { text: "Time" }), el("span", { class: "muted", text: "Pakistan time" })),
      presets,
      el("div", { class: "ex-range" },
        el("label", {}, el("span", { text: "From" }), startIn),
        el("label", {}, el("span", { text: "To" }), endIn)),
      el("p", { class: "help", text: meta.max_days ? `Up to ${meta.max_days} days at a time.` : "Any date range. Long ranges take longer." })),
    el("section", { class: "ex-block" },
      el("div", { class: "ex-h" }, el("h2", { text: `Filters${cfg.filters.length ? ` (${cfg.filters.length})` : ""}` }), addFilter),
      filters),
    el("section", { class: "ex-block" },
      el("div", { class: "ex-h" }, el("h2", { text: "Leave out" })),
      toggle("identified_only", "Unidentified subscribers",
        "Keep only records where the network identified the subscriber (leaves out '<ip>_<port>' ids, about 45% of records)."),
      toggle("hide_background", "Background traffic",
        "Leave out background and infrastructure traffic (SSL, GoogleAPI and similar labels) that is not an app a person opens.")),
    el("div", { class: "ex-actions" }, runBtn, reset),
    el("p", { class: "status-line", id: "explore-status", role: "status" })));
  markStale();
}

function drawToolbar() {
  const bar = $("#ex-toolbar", viewEl);
  if (!bar) return;
  const tabs = el("div", { class: "ex-tabs", role: "tablist", "aria-label": "Show" },
    [["records", "Records"], ["summary", "Summary"]].map(([v, t]) => el("button", {
      type: "button", role: "tab", class: `ex-tab${cfg.mode === v ? " active" : ""}`, "aria-selected": String(cfg.mode === v), text: t,
      onclick: () => { if (cfg.mode === v) return; cfg.mode = v; drawToolbar(); changed(true); },
    })));

  const tools = el("div", { class: "ex-tools" });
  const select = (label, options, value, onChange) => {
    const s = el("select", { class: "input input-sm", "aria-label": label }, options.map(([v, t]) => el("option", { value: v, text: t })));
    s.value = value;
    s.addEventListener("change", () => onChange(s.value));
    return s;
  };
  const plainFields = meta.fields.filter((f) => f.kind !== "group");
  if (cfg.mode === "records") {
    const fieldItems = plainFields.map((f) => ({ key: f.name, text: f.name, title: f.description, note: f.type, mono: true }));
    const fields = checkMenu(() => `Fields (${cfg.columns.length})`, fieldItems, (k) => cfg.columns.includes(k), (k, on) => {
      cfg.columns = on ? plainFields.map((x) => x.name).filter((n) => n === k || cfg.columns.includes(n)) : cfg.columns.filter((n) => n !== k);
      changed(true);
    }, (refresh) => el("div", { class: "picker-actions" },
      el("button", { type: "button", class: "link", text: "Default", onclick: () => { cfg.columns = [...meta.default_columns]; refresh(); changed(true); } }),
      el("button", { type: "button", class: "link", text: "All", onclick: () => { cfg.columns = plainFields.map((f) => f.name); refresh(); changed(true); } })));
    tools.append(
      fields,
      el("span", { class: "ex-tool-label", text: "Sort" }),
      select("Sort by", plainFields.map((f) => [f.name, f.name]), cfg.sort, (v) => { cfg.sort = v; changed(true); }),
      select("Order", [["desc", "newest / highest first"], ["asc", "oldest / lowest first"]], cfg.desc ? "desc" : "asc", (v) => { cfg.desc = v === "desc"; changed(true); }),
      select("Rows per page", [50, 100, 250, 500, 1000].map((n) => [String(n), `${n} rows`]), String(cfg.limit), (v) => { cfg.limit = Number(v); changed(true); }));
  } else {
    const groupOpts = [["", "nothing"], ...meta.time_groups.map((g) => [g.key, `${g.label.toLowerCase()} (time)`]),
      ...meta.fields.filter((f) => f.kind !== "time" && f.kind !== "group").map((f) => [f.name, f.name])];
    tools.append(el("span", { class: "ex-tool-label", text: "Group by" }));
    for (let i = 0; i < 3; i++) {
      if (i > 0 && !cfg.group_by[i - 1]) break;
      if (i > 0) tools.append(el("span", { class: "ex-tool-label", text: "then" }));
      tools.append(select(`Group by ${i + 1}`, i ? [["", "—"], ...groupOpts.slice(1)] : groupOpts, cfg.group_by[i] || "", (v) => {
        const g = [...cfg.group_by];
        g[i] = v;
        cfg.group_by = g.slice(0, v ? 3 : i).filter(Boolean);
        drawToolbar(); changed(true);
      }));
    }
    const measureItems = meta.measures.map((m) => ({ key: m.key, text: m.label }));
    tools.append(
      checkMenu(() => `Measures (${cfg.measures.length})`, measureItems, (k) => cfg.measures.includes(k), (k, on) => {
        cfg.measures = meta.measures.map((x) => x.key).filter((x) => (x === k ? on : cfg.measures.includes(x)));
        changed(true);
      }),
      select("Rows", [20, 50, 100, 500, 1000].map((n) => [String(n), `top ${n}`]), String(cfg.limit), (v) => { cfg.limit = Number(v); changed(true); }));
  }
  const exportBtn = el("button", { type: "button", class: "btn btn-sm", onclick: exportCsv, title: `Everything that matches, up to ${nf.format(meta.max_export || 200000)} rows` });
  exportBtn.innerHTML = `${icon("download", "sm")}<span>CSV</span>`;
  bar.replaceChildren(tabs, tools, el("span", { class: "spacer" }), exportBtn);
}

function emptyState(message) {
  const box = $("#explore-results", viewEl);
  if (!box) return;
  box.replaceChildren(el("div", { class: "ex-empty" },
    el("h3", { text: message || "Nothing to show yet" }),
    el("p", { text: "Look up a phone number, IP, website, app, operator or city above, or set the time and filters on the left and press Run." }),
    el("div", { class: "ex-examples" }, EXAMPLES.map((x) => el("button", { type: "button", class: "chip-btn", text: x, onclick: () => lookup(x) })))));
}

function searchSummary() {
  const out = [];
  const p = activePreset();
  out.push(p ? PRESETS.find(([k]) => k === p)[1] : `${cfg.start.replace("T", " ")} to ${cfg.end.replace("T", " ")}`);
  out.push(cfg.filters.length ? `${cfg.filters.length} filter${cfg.filters.length > 1 ? "s" : ""}` : "no filters");
  if (cfg.identified_only) out.push("identified only");
  if (cfg.hide_background) out.push("no background");
  return out;
}

function status(text, kind = "") {
  const s = $("#explore-status", viewEl);
  if (s) { s.textContent = text; s.className = `status-line ${kind}`.trim(); delete s.dataset.stale; }
}

function body(c = cfg) {
  const { lookup: _l, lookupNote: _n, ...rest } = c;
  return rest;
}

let runSeq = 0;

async function run(offset = cfg.offset) {
  cfg.offset = Math.max(0, offset);
  saveState();
  const snap = { ...cfg, filters: cfg.filters.map((f) => ({ ...f })) };
  const seq = ++runSeq;
  status("Running…");
  const box = $("#explore-results", viewEl);
  if (box) box.classList.add("loading");
  if (snap.mode === "records") refreshCount(snap);
  try {
    const data = await send("POST", "/api/explore/run", body(snap));
    if (seq !== runSeq) return;
    lastRun = { mode: snap.mode, data, snap };
    status("");
    if (snap.mode === cfg.mode) showResult(data, snap);
    markStale();
    const sum = viewEl.querySelector(".ex-fold-sum");
    if (sum) sum.textContent = searchSummary().join(" · ");
  } catch (e) {
    if (seq === runSeq) {
      status(e.message, "bad");
      if (!lastRun) emptyState("That search did not run");
    }
  } finally {
    if (box && seq === runSeq) box.classList.remove("loading");
  }
}

async function refreshCount(snap) {
  const k = countKey(snap);
  if (lastCount && lastCount.key === k) return;
  lastCount = { key: k, pending: true };
  try {
    const out = await send("POST", "/api/explore/count", body(snap));
    if (lastCount && lastCount.key === k) lastCount = { key: k, count: out.count };
  } catch {
    if (lastCount && lastCount.key === k) lastCount = null;
  }
  const s = viewEl && $("#explore-stats", viewEl);
  if (s && lastRun && lastRun.mode === "records" && countKey(lastRun.snap) === k) s.textContent = recordStats(lastRun.data.result, lastRun.snap);
}

function recordStats(r, snap) {
  const offset = snap.offset || 0;
  const k = countKey(snap);
  const total = lastCount && lastCount.key === k
    ? (lastCount.pending ? " of …" : ` of ${nf.format(lastCount.count)}`) : "";
  const range = r.row_count ? `Rows ${nf.format(offset + 1)}–${nf.format(offset + r.row_count)}${total}` : "No rows";
  return `${range} · ${r.elapsed_s} s · ${compact(r.rows_read)} records read`;
}

function exportCsv() {
  const url = `/api/explore/export?q=${encodeURIComponent(JSON.stringify(body()))}`;
  const a = el("a", { href: url, download: "" });
  document.body.append(a); a.click(); a.remove();
  toast("Export started. Large exports can take a minute.");
}

async function saveView() {
  const name = prompt("Name for this view (time range, filters and display are saved):");
  if (!name || !name.trim()) return;
  const { offset, lookupNote, ...config } = cfg;
  try { await send("POST", "/api/views", { name: name.trim(), config }); toast("View saved"); drawTop(); } catch (e) { toast(e.message); }
}


function filterFor(col, value, exclude = false) {
  const f = fieldByName(col);
  if (!f || f.kind === "time" || f.kind === "group" || value === null || value === undefined) return null;
  if (value === "") return { field: col, op: exclude ? "not_empty" : "empty", value: "" };
  return { field: col, op: exclude ? "ne" : "eq", value: String(value) };
}

function applyFilters(list, extra = {}) {
  for (const f of list) {
    if (f.op === "eq") cfg.filters = cfg.filters.filter((x) => !(x.field === f.field && x.op === "eq"));
    if (!cfg.filters.some((x) => x.field === f.field && x.op === f.op && x.value === f.value)) cfg.filters.push(f);
  }
  Object.assign(cfg, extra, { offset: 0, lookupNote: "" });
  saveState();
  draw();
  run(0);
}

function drillDown(snap, row, columns) {
  const list = [];
  const extra = { mode: "records" };
  let skipped = false;
  snap.group_by.forEach((g) => {
    const i = columns.findIndex((c) => c.name === g);
    if (i < 0) return;
    const v = row[i];
    if (g === "day") { const s = parseWall(String(v)); extra.start = toLocalInput(s); extra.end = toLocalInput(new Date(s.getTime() + 86400000)); }
    else if (g === "hour") { const s = parseWall(String(v).slice(0, 16)); extra.start = toLocalInput(s); extra.end = toLocalInput(new Date(s.getTime() + 3600000)); }
    else if (g === "hour_of_day") skipped = true;
    else { const f = filterFor(g, v); if (f) list.push(f); }
  });
  applyFilters(list, extra);
  if (skipped) toast("Hour of day spans every day, so it is not applied as a filter.");
}

let menuEl = null;
let menuAnchor = null;

function closeMenu() {
  if (!menuEl) return;
  menuEl.remove();
  menuEl = null;
  document.removeEventListener("pointerdown", onOutside, true);
  document.removeEventListener("keydown", onMenuKey, true);
  window.removeEventListener("scroll", closeMenu, true);
}
function onOutside(e) { if (menuEl && !menuEl.contains(e.target)) closeMenu(); }
function onMenuKey(e) {
  if (!menuEl) return;
  const items = [...menuEl.querySelectorAll("button")];
  const at = items.indexOf(document.activeElement);
  if (e.key === "Escape") { e.preventDefault(); closeMenu(); menuAnchor?.focus(); }
  else if (e.key === "ArrowDown") { e.preventDefault(); items[(at + 1) % items.length]?.focus(); }
  else if (e.key === "ArrowUp") { e.preventDefault(); items[(at - 1 + items.length) % items.length]?.focus(); }
  else if (e.key === "Tab") closeMenu();
}

function openMenu(anchor, title, items) {
  closeMenu();
  menuAnchor = anchor;
  menuEl = el("div", { class: "cell-menu", role: "menu", "aria-label": title },
    el("div", { class: "cell-menu-title", text: title }),
    items.map((it) => el("button", { type: "button", role: "menuitem", class: "cell-menu-item", text: it.label,
      onclick: () => { closeMenu(); it.run(); } })));
  document.body.append(menuEl);
  const r = anchor.getBoundingClientRect();
  const w = menuEl.offsetWidth, h = menuEl.offsetHeight;
  const top = r.bottom + 4 + h > window.innerHeight ? Math.max(8, r.top - h - 4) : r.bottom + 4;
  menuEl.style.top = `${top}px`;
  menuEl.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - w - 8))}px`;
  menuEl.querySelector("button")?.focus();
  document.addEventListener("pointerdown", onOutside, true);
  document.addEventListener("keydown", onMenuKey, true);
  window.addEventListener("scroll", closeMenu, true);
}

function cellMenu(data, snap, rowIndex, colIndex, td) {
  const r = data.result;
  const col = r.columns[colIndex];
  const v = r.rows[rowIndex][colIndex];
  const items = [];
  if (snap.mode === "records" && data.keys && data.keys[rowIndex]) {
    const [t, id] = data.keys[rowIndex];
    items.push({ label: "Open the full record", run: () => openRecord(t, id) });
  }
  if (snap.mode === "summary" && snap.group_by.length) {
    items.push({ label: "Show the records behind this row", run: () => drillDown(snap, r.rows[rowIndex], r.columns) });
  }
  const keep = filterFor(col.name, v);
  if (keep) {
    const shown = v === "" ? "(empty)" : `“${short(full(v), 28)}”`;
    items.push({ label: `Only ${pretty(col.name).toLowerCase()} ${shown}`, run: () => applyFilters([keep]) });
    items.push({ label: `Leave out ${shown}`, run: () => applyFilters([filterFor(col.name, v, true)]) });
  }
  items.push({ label: "Copy value", run: () => copyText(v === null ? "" : String(v)).then(() => toast("Copied")) });
  openMenu(td, `${pretty(col.name)}: ${short(v === null ? "null" : full(v), 40)}`, items);
}


let drawerEl = null;

const sizeText = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(2)} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : n >= 1e3 ? `${(n / 1e3).toFixed(1)} KB` : `${n} B`);

function readable(name, v) {
  if (v === "" || v === null || v === undefined) return "(empty)";
  const s = full(v);
  if (typeof v !== "number") return s;
  if (name === "duration") return `${s} ms (${(v / 1000).toFixed(v < 10000 ? 2 : 1)} s)`;
  if (name.endsWith("_byte_num")) return `${s} bytes (${sizeText(v)})`;
  return s;
}

function closeDrawer() {
  if (!drawerEl) return;
  drawerEl.remove();
  drawerEl = null;
  document.removeEventListener("keydown", onDrawerKey);
}
function onDrawerKey(e) { if (e.key === "Escape" && !menuEl) closeDrawer(); }

async function openRecord(t, id) {
  closeDrawer();
  const closeBtn = el("button", { type: "button", class: "btn btn-sm btn-ghost", text: "Close", onclick: closeDrawer });
  const bodyEl = el("div", { class: "drawer-body" }, el("p", { class: "muted", text: "Loading the record…" }));
  drawerEl = el("aside", { class: "drawer", role: "dialog", "aria-label": "Record details" },
    el("div", { class: "drawer-head" }, el("div", {}, el("h2", { text: "Record" }), el("p", { class: "muted mono", text: `${t} · log ${id}` })), closeBtn),
    bodyEl);
  document.body.append(drawerEl);
  document.addEventListener("keydown", onDrawerKey);
  closeBtn.focus();
  let data;
  try {
    data = await send("POST", "/api/explore/record", { t, id });
  } catch (e) {
    bodyEl.replaceChildren(el("p", { class: "callout bad", text: e.message }));
    return;
  }
  if (!drawerEl) return;
  const filled = data.fields.filter((f) => f.value !== "" && f.value !== null);
  const empty = data.fields.length - filled.length;
  const row = (f) => {
    const act = [];
    const keep = f.name === "log_id" ? null : filterFor(f.name, f.value);
    if (keep) act.push(el("button", { type: "button", class: "link", text: "Filter", title: `Show only records where ${f.name} is this value`,
      onclick: () => { closeDrawer(); applyFilters([keep], { mode: "records" }); } }));
    act.push(el("button", { type: "button", class: "icon-btn", title: "Copy", "aria-label": `Copy ${f.name}`, html: icon("copy", "sm"),
      onclick: () => copyText(String(f.value ?? "")).then(() => toast("Copied")) }));
    return el("div", { class: "record-field" },
      el("div", { class: "record-name", title: f.description || "" }, el("span", { text: pretty(f.name) }), el("span", { class: "mono muted", text: f.name })),
      el("div", { class: "record-value mono", text: readable(f.name, f.value) }),
      el("div", { class: "record-actions" }, act));
  };
  const list = el("div", { class: "record-fields" }, filled.map(row));
  const parts = [list];
  if (empty) {
    const more = el("button", { type: "button", class: "link", text: `Show ${empty} empty fields` });
    more.addEventListener("click", () => { list.replaceChildren(...data.fields.map(row)); more.remove(); });
    parts.push(more);
  }
  bodyEl.replaceChildren(...parts);
}


function showResult(data, snap = cfg) {
  const box = $("#explore-results", viewEl);
  if (!box) return;
  const r = data.result;
  const offset = snap.offset || 0;
  const limit = snap.limit || cfg.limit;
  disposeChart(chart);
  chart = null;
  const sqlPre = el("pre", { class: "sql-view" });
  sqlPre.append(highlightSql(data.sql));
  const sqlBox = el("details", { class: "sql-details" }, el("summary", { text: "SQL that ran" }), sqlPre);

  const tableWrap = el("div", { class: "table-wrap tall" });
  const tableNote = el("p", { class: "note", hidden: true });
  renderTable(tableWrap, tableNote, r, { onCell: (ri, ci, td) => cellMenu(data, snap, ri, ci, td) });
  const tip = el("span", { class: "help table-tip", text: snap.mode === "records"
    ? "Click a value to filter on it or open the full record."
    : "Click a row's value to see the records behind it." });

  if (!r.row_count) {
    box.replaceChildren(el("div", { class: "ex-empty" },
      el("h3", { text: "No records match" }),
      el("p", { text: "Widen the time range (try Last 7 days), loosen a filter, or turn off a Leave out switch." })),
      sqlBox);
    return;
  }

  if (snap.mode === "records") {
    const page = Math.floor(offset / limit) + 1;
    const prev = el("button", { type: "button", class: "btn btn-sm", text: "Previous", disabled: offset === 0, onclick: () => run(offset - limit) });
    const next = el("button", { type: "button", class: "btn btn-sm", text: "Next", disabled: r.row_count < limit, onclick: () => run(offset + limit) });
    box.replaceChildren(
      el("div", { class: "results-head" },
        el("span", { class: "result-stats", id: "explore-stats", text: recordStats(r, snap) }), tip,
        el("div", { class: "pager" }, prev, el("span", { class: "muted", text: `Page ${page}` }), next)),
      tableWrap, tableNote, sqlBox);
    return;
  }

  const stats = [`${nf.format(r.row_count)} ${r.row_count === 1 ? "row" : "rows"}`, `${r.elapsed_s} s`, `${compact(r.rows_read)} records read`];
  const options = (data.charts && data.charts.options) || [];
  const chartBox = el("div", { class: "chart" });
  const chartNote = el("p", { class: "note", hidden: true });
  const select = chartSelect(options, { class: "input input-sm" });
  select.value = String((data.charts && data.charts.default) ?? 0);
  const drawIt = () => {
    disposeChart(chart);
    if (!options.length) return;
    const out = drawChart(chartBox, r, options[Number(select.value) || 0]);
    chart = out.instance;
    chartNote.textContent = out.notes.join(" ");
    chartNote.hidden = !out.notes.length;
  };
  select.addEventListener("change", drawIt);
  box.replaceChildren(
    el("div", { class: "results-head" }, el("span", { class: "result-stats", text: stats.join(" · ") }),
      snap.group_by.length ? tip : null,
      options.length > 1 ? el("div", { class: "chart-picker" }, select) : null),
    options.length ? chartBox : null, chartNote,
    tableWrap, tableNote, sqlBox);
  requestAnimationFrame(drawIt);
}

export function exploreLabel(name) { return pretty(name); }

onThemeChange(() => {
  if (viewEl && viewEl.isConnected && !viewEl.hidden && lastRun && lastRun.mode === cfg.mode) showResult(lastRun.data, lastRun.snap);
});

window.addEventListener("hashchange", () => { closeMenu(); closeDrawer(); });
