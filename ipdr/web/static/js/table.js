
import { el, full, isNumType, nf, pretty } from "./util.js";

const MAX_ROWS = 500;

export function renderTable(wrap, note, result, opts = {}) {
  const { columns, rows } = result;
  const indexOf = new Map(rows.map((r, i) => [r, i]));
  const numeric = columns.map((c) => isNumType(c.type));
  const wide = columns.map((c, i) => !numeric[i] && rows.slice(0, 50).some((r) => String(r[i] ?? "").length > 48));
  let sortCol = -1;
  let dir = 1;
  const table = el("table", { class: "data" });

  const draw = () => {
    let view = rows;
    if (sortCol >= 0) {
      view = [...rows].sort((a, b) => {
        const x = a[sortCol], y = b[sortCol];
        if (x === y) return 0;
        if (x === null || x === undefined) return 1;
        if (y === null || y === undefined) return -1;
        return (numeric[sortCol] ? x - y : String(x).localeCompare(String(y))) * dir;
      });
    }
    const head = el("tr", {}, columns.map((c, i) => {
      const btn = el("button", { type: "button", class: "th-sort", title: `Sort by ${pretty(c.name)} (${c.name} · ${c.type})`, text: pretty(c.name) });
      const th = el("th", { class: numeric[i] ? "num" : "", scope: "col" }, btn);
      if (i === sortCol) th.setAttribute("aria-sort", dir > 0 ? "ascending" : "descending");
      btn.addEventListener("click", () => {
        dir = sortCol === i ? -dir : (numeric[i] ? -1 : 1);
        sortCol = i;
        draw();
        table.querySelectorAll(".th-sort")[i]?.focus();
      });
      return th;
    }));
    const body = el("tbody", {}, view.slice(0, MAX_ROWS).map((row) => el("tr", {}, row.map((v, i) => {
      const td = el("td", {
        class: [numeric[i] ? "num" : "", wide[i] ? "wrap" : "", v === null ? "null" : "", opts.onCell ? "cell-link" : ""].join(" ").trim() || null,
        text: v === null ? "null" : full(v),
      });
      if (opts.onCell) {
        td.tabIndex = 0;
        const open = () => opts.onCell(indexOf.get(row), i, td);
        td.addEventListener("click", open);
        td.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } });
      }
      return td;
    }))));
    table.replaceChildren(el("thead", {}, head), body);
  };
  draw();
  wrap.replaceChildren(table);
  note.hidden = rows.length <= MAX_ROWS;
  note.textContent = `Showing the first ${MAX_ROWS} of ${nf.format(rows.length)} rows. The CSV has all of them.`;
}
