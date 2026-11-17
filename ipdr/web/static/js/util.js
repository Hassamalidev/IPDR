
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v === null || v === undefined) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(node.dataset, v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : String(c));
  }
  return node;
}

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export const nf = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
export const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function full(v) {
  if (v === null || v === undefined) return "";
  if (Array.isArray(v)) return v.map(full).join(", ");
  return typeof v === "number" ? nf.format(v) : String(v);
}

export function compact(v) {
  if (typeof v !== "number") return v;
  const a = Math.abs(v);
  const one = (x, s) => `${Math.round(x * 10) / 10}${s}`;
  if (a >= 1e9) return one(v / 1e9, "B");
  if (a >= 1e6) return one(v / 1e6, "M");
  if (a >= 1e4) return one(v / 1e3, "K");
  return nf.format(v);
}

export function pretty(name) {
  const units = { gb: "GB", mb: "MB", kb: "KB", s: "s", ms: "ms", pct: "%" };
  const parts = String(name).split("_");
  let unit = "";
  if (parts.length > 1 && units[parts[parts.length - 1].toLowerCase()]) unit = ` (${units[parts.pop().toLowerCase()]})`;
  const text = parts.join(" ").trim();
  return text ? text[0].toUpperCase() + text.slice(1) + unit : name;
}

export function fmtDay(s) {
  if (!s) return "";
  const [y, m, d] = s.slice(0, 10).split("-").map(Number);
  return y ? `${d} ${MONTHS[m - 1]} ${y}` : s;
}

export function fmtDateTime(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export const isNumType = (t) => /^(Nullable\()?(LowCardinality\()?(U?Int\d+|Float\d+|Decimal)/.test(t || "");
export const isDateTime = (t) => /DateTime/.test(t || "");
export const isDate = (t) => /^(Nullable\()?Date(32)?\)?$/.test(t || "");

export function fmtAxisValue(v, type) {
  if (v === null || v === undefined) return "";
  const s = String(v);
  if (isDate(type) || isDateTime(type)) {
    const [y, m, d] = s.slice(0, 10).split("-").map(Number);
    if (!y) return s;
    const day = `${d} ${MONTHS[m - 1]}`;
    return isDateTime(type) && s.length > 10 ? `${day} ${s.slice(11, 16)}` : day;
  }
  return s;
}

export function recencyGroup(iso) {
  const d = new Date(iso);
  const now = new Date();
  const startOfDay = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOfDay(now) - startOfDay(d)) / 86400000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return "Previous 7 days";
  if (days < 30) return "Previous 30 days";
  return d.toLocaleString("en-GB", { month: "long", year: "numeric" });
}

let toastTimer = null;
export function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 2600);
}

export function debounce(fn, ms = 250) {
  let id;
  return (...args) => { clearTimeout(id); id = setTimeout(() => fn(...args), ms); };
}

export async function copyRich(text, html) {
  try {
    if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
      await navigator.clipboard.write([new ClipboardItem({
        "text/plain": new Blob([text], { type: "text/plain" }),
        "text/html": new Blob([html], { type: "text/html" }),
      })]);
      toast("Answer copied");
      return;
    }
  } catch {  }
  await copyText(text);
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied");
  } catch {
    toast("Could not copy. Select the text and copy it yourself.");
  }
}

export function download(href, filename) {
  const a = el("a", { href, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
}


const KEYWORDS = new Set(`select from where and or not in is null as on join left right inner outer full cross using
group by order having limit offset with union all distinct case when then else end between like ilike asc desc
prewhere array global any interval if exists`.split(/\s+/));

export function highlightSql(sql) {
  const frag = document.createDocumentFragment();
  const re = /(--[^\n]*|\/\*[\s\S]*?\*\/)|('(?:[^'\\]|\\.|'')*')|(\b\d+(?:\.\d+)?(?:e[+-]?\d+)?\b)|([A-Za-z_][\w]*)(?=\s*\()|([A-Za-z_][\w]*)|(\s+|.)/gi;
  let m;
  while ((m = re.exec(sql)) !== null) {
    const [tok, cm, str, num, fn, word] = m;
    let cls = "";
    if (cm) cls = "cm";
    else if (str) cls = "str";
    else if (num) cls = "num";
    else if (fn) cls = KEYWORDS.has(fn.toLowerCase()) ? "kw" : "fn";
    else if (word && KEYWORDS.has(word.toLowerCase())) cls = "kw";
    frag.append(cls ? el("span", { class: cls, text: tok }) : document.createTextNode(tok));
  }
  return frag;
}
