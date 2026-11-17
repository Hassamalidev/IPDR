
import { $, $$ } from "./util.js";
import { icon } from "./icons.js";

const KEY = "ipdr.theme";
const listeners = new Set();

export function getTheme() {
  try { return localStorage.getItem(KEY) || "system"; } catch { return "system"; }
}

export function isDark() {
  const t = getTheme();
  if (t === "dark") return true;
  if (t === "light") return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function setTheme(t) {
  try { t === "system" ? localStorage.removeItem(KEY) : localStorage.setItem(KEY, t); } catch {  }
  apply();
}

export function onThemeChange(fn) {
  listeners.add(fn);
}

function apply() {
  const t = getTheme();
  if (t === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
  const btn = $("#theme-btn");
  if (btn) {
    btn.innerHTML = icon(t === "system" ? "monitor" : isDark() ? "moon" : "sun");
    btn.title = `Theme: ${t === "system" ? "same as system" : t}`;
  }
  $$("[data-theme-choice]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.themeChoice === t)));
  document.querySelectorAll('input[name="theme"]').forEach((r) => { r.checked = r.value === t; });
  listeners.forEach((fn) => fn());
}

export function initTheme() {
  const btn = $("#theme-btn");
  const menu = $("#theme-menu");
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = menu.hidden;
    menu.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("click", () => { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); });
  menu.addEventListener("click", (e) => {
    const b = e.target.closest("[data-theme-choice]");
    if (b) setTheme(b.dataset.themeChoice);
  });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (getTheme() === "system") apply(); });
  apply();
}
