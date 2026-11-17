
import { compact, el, escapeHtml, full, fmtAxisValue, isDate, isDateTime, isNumType, pretty } from "./util.js";

const MAX_BARS = 25;
const MAX_SERIES = 8;
const FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif';
export const live = new Set();

export function tokens() {
  const s = getComputedStyle(document.documentElement);
  const g = (n) => s.getPropertyValue(n).trim();
  return {
    ink1: g("--ink-1"), ink2: g("--ink-2"), ink3: g("--ink-3"),
    grid: g("--grid"), axis: g("--axis"), surface: g("--surface"), other: g("--other"),
    series: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => g(`--s${i}`)),
    seq: [g("--seq-lo"), g("--seq-mid"), g("--seq-hi")],
  };
}

function base(t) {
  return {
    animationDuration: 250,
    textStyle: { fontFamily: FONT, color: t.ink2, fontSize: 12 },
    grid: { left: 8, right: 28, top: 40, bottom: 8, containLabel: true },
    tooltip: {
      backgroundColor: t.surface, borderColor: t.axis, borderWidth: 1, padding: [6, 10],
      textStyle: { color: t.ink1, fontSize: 12, fontFamily: FONT },
      extraCssText: "box-shadow:none;border-radius:6px;",
      valueFormatter: full,
      confine: true,
    },
  };
}

const valueAxis = (t, name, extra = {}, along = "y") => ({
  type: "value", name,
  nameLocation: along === "y" ? "end" : "middle",
  nameGap: along === "y" ? 14 : 30,
  nameTextStyle: { color: t.ink3, fontSize: 12, align: along === "y" ? "left" : "center" },
  axisLabel: { color: t.ink3, formatter: compact },
  splitLine: { lineStyle: { color: t.grid, width: 1, type: "solid" } },
  axisLine: { show: false }, axisTick: { show: false },
  ...extra,
});

function categoryAxis(t, data, extra = {}) {
  const { axisLabel, ...rest } = extra;
  return {
    type: "category", data,
    axisLabel: { color: t.ink2, hideOverlap: true, ...(axisLabel || {}) },
    axisLine: { lineStyle: { color: t.axis } }, axisTick: { show: false },
    ...rest,
  };
}

const legend = (t, show) => (show
  ? { top: 0, left: 0, icon: "roundRect", itemWidth: 10, itemHeight: 10, textStyle: { color: t.ink2 } }
  : { show: false });

function pivot(rows, xi, si, yi, additive, maxX = Infinity) {
  const xs = [], xIndex = new Map(), totals = new Map(), cells = new Map();
  for (const r of rows) {
    const x = r[xi], s = si >= 0 ? String(r[si] ?? "") : "", v = Number(r[yi]) || 0;
    if (!xIndex.has(x)) { xIndex.set(x, xs.length); xs.push(x); }
    totals.set(s, (totals.get(s) || 0) + v);
    const key = `${xIndex.get(x)}\u0000${s}`;
    cells.set(key, (cells.get(key) || 0) + v);
  }
  let names = [...totals.keys()];
  let dropped = [];
  if (names.length > MAX_SERIES) {
    const ranked = [...names].sort((a, b) => totals.get(b) - totals.get(a));
    const keep = new Set(ranked.slice(0, MAX_SERIES - (additive ? 1 : 0)));
    dropped = names.filter((n) => !keep.has(n));
    names = names.filter((n) => keep.has(n));
  }
  const used = xs.slice(0, maxX);
  const series = names.map((n) => ({ name: n, data: used.map((_, i) => cells.get(`${i}\u0000${n}`) ?? null) }));
  if (dropped.length && additive) {
    series.push({ name: "Other", other: true, data: used.map((_, i) => dropped.reduce((a, n) => a + (cells.get(`${i}\u0000${n}`) || 0), 0)) });
  }
  return { xs: used, xTotal: xs.length, series, dropped };
}

const colorOf = (t, s, i) => (s.other ? t.other : t.series[i % t.series.length]);

function ranked(rows, xi, yi, label = String) {
  const sums = new Map();
  for (const r of rows) {
    const k = label(r[xi] ?? "");
    sums.set(k, (sums.get(k) || 0) + (Number(r[yi]) || 0));
  }
  return [...sums].map(([name, value]) => ({ name, value })).sort((a, b) => b.value - a.value);
}

const sum = (items) => items.reduce((a, d) => a + d.value, 0);
const pct = (v, total) => (total ? `${Math.round((v / total) * 1000) / 10}%` : "");
const ordered = (vals) => (vals.every((v) => typeof v === "number") ? [...vals].sort((a, b) => a - b)
  : vals.every((v) => /^\d{4}-\d{2}-\d{2}/.test(String(v))) ? [...vals].sort() : vals);

const FAMILIES = [
  ["Compare and rank", ["figure", "bar", "column", "pareto"]],
  ["Share of a whole", ["pie", "treemap", "stacked_bar", "percent_bar", "sunburst"]],
  ["Over time", ["line", "area", "stacked_area", "calendar", "clock"]],
  ["Patterns and flows", ["heatmap", "sankey"]],
  ["Several measures", ["combo", "radar", "scatter", "bubble"]],
  ["Location", ["map"]],
];
export function chartSelect(options, attrs = {}) {
  const select = el("select", { "aria-label": "Chart type", ...attrs });
  const option = (o) => el("option", { value: String(options.indexOf(o)), text: o.title });
  if (options.length < 6) { options.forEach((o) => select.append(option(o))); return select; }
  const placed = new Set();
  for (const [label, kinds] of FAMILIES) {
    const mine = options.filter((o) => kinds.includes(o.type) && !placed.has(o));
    if (!mine.length) continue;
    mine.forEach((o) => placed.add(o));
    select.append(el("optgroup", { label }, mine.map(option)));
  }
  const rest = options.filter((o) => !placed.has(o));
  if (rest.length) select.append(el("optgroup", { label: "Other" }, rest.map(option)));
  return select;
}

export function optionOfType(options, type) {
  return options.findIndex((o) => o.type === type);
}

export function drawChart(box, result, opt) {
  box.replaceChildren();
  box.className = "chart";
  box.style.height = "";
  const notes = [];
  const t = tokens();
  const { columns, rows } = result;
  const idx = (name) => columns.findIndex((c) => c.name === name);
  const typeOf = (name) => (columns[idx(name)] || {}).type || "";
  const xi = idx(opt.x), yi = idx(opt.y), si = idx(opt.series), vi = idx(opt.value), li = idx(opt.label);

  if (opt.type === "figure") {
    box.className = "figure";
    box.append(el("div", { class: "figure-value", text: full(rows[0][yi]) }), el("div", { class: "figure-label", text: pretty(opt.y) }));
    return { instance: null, notes };
  }

  const option = base(t);
  let height = 380;
  const xLabels = (xs) => xs.map((v) => fmtAxisValue(v, typeOf(opt.x)));
  const orderedX = isDate(typeOf(opt.x)) || isDateTime(typeOf(opt.x)) || isNumType(typeOf(opt.x));
  const shadow = { type: "shadow", shadowStyle: { color: "rgba(127,127,127,0.08)" } };

  const percent = opt.type === "percent_bar";
  const toShares = (p) => {
    p.xs.forEach((_, i) => {
      const total = p.series.reduce((a, s) => a + (s.data[i] || 0), 0);
      p.series.forEach((s) => { s.data[i] = total ? Math.round(((s.data[i] || 0) / total) * 1000) / 10 : null; });
    });
    return p;
  };
  const shareAxis = { min: 0, max: 100, axisLabel: { color: t.ink3, formatter: (v) => `${v}%` } };
  const esc = escapeHtml;

  if (opt.type === "bar" || ((opt.type === "stacked_bar" || percent) && !orderedX)) {
    const stacked = opt.type === "stacked_bar" || percent;
    const p = pivot(rows, xi, si, yi, opt.additive, MAX_BARS);
    if (percent) toShares(p);
    const n = p.xs.length;
    const multi = p.series.length > 1;
    height = Math.max(220, Math.min(900, n * (multi && !stacked ? 16 * p.series.length + 12 : 30) + 80));
    option.legend = legend(t, multi);
    option.grid.top = multi ? 40 : 20;
    option.grid.bottom = 32;
    option.yAxis = categoryAxis(t, xLabels(p.xs), { inverse: true, axisLabel: { width: 200, overflow: "truncate" } });
    option.xAxis = valueAxis(t, percent ? `Share of ${pretty(opt.y).toLowerCase()} (%)` : pretty(opt.y), percent ? shareAxis : {}, "x");
    option.tooltip.trigger = "axis";
    option.tooltip.axisPointer = shadow;
    if (percent) option.tooltip.valueFormatter = (v) => (v === null || v === undefined ? "" : `${v}%`);
    option.series = p.series.map((s, i) => ({
      name: multi ? s.name : pretty(opt.y), type: "bar", data: s.data, barMaxWidth: 24,
      stack: stacked ? "total" : undefined,
      itemStyle: { color: colorOf(t, s, i), borderRadius: stacked ? 0 : [0, 4, 4, 0], borderColor: t.surface, borderWidth: stacked ? 1 : 0 },
      label: { show: !multi && n <= 15, position: "right", color: t.ink2, formatter: (q) => compact(q.value) },
    }));
    if (p.xTotal > n) notes.push(`Showing the first ${n} of ${p.xTotal}. The table has all of them.`);
    if (p.dropped.length && !opt.additive) notes.push(`Showing the ${p.series.length} largest of ${p.series.length + p.dropped.length} groups. User counts cannot be added into an "Other" group.`);
  } else if (opt.type === "column" || opt.type === "stacked_bar" || percent) {
    const stacked = opt.type === "stacked_bar" || percent;
    const p = pivot(rows, xi, si, yi, opt.additive, orderedX ? Infinity : MAX_BARS);
    if (percent) toShares(p);
    const multi = p.series.length > 1;
    option.legend = legend(t, multi);
    option.xAxis = categoryAxis(t, xLabels(p.xs), orderedX ? {} : { axisLabel: { rotate: p.xs.length > 5 ? 30 : 0, width: 110, overflow: "truncate" } });
    option.yAxis = valueAxis(t, percent ? `Share of ${pretty(opt.y).toLowerCase()} (%)` : pretty(opt.y), percent ? shareAxis : {});
    option.tooltip.trigger = "axis";
    option.tooltip.axisPointer = shadow;
    if (percent) option.tooltip.valueFormatter = (v) => (v === null || v === undefined ? "" : `${v}%`);
    if (p.xTotal > p.xs.length) notes.push(`Showing the first ${p.xs.length} of ${p.xTotal}. The table has all of them.`);
    option.series = p.series.map((s, i) => ({
      name: multi ? s.name : pretty(opt.y), type: "bar", data: s.data, barMaxWidth: 24,
      stack: stacked ? "total" : undefined,
      itemStyle: { color: colorOf(t, s, i), borderRadius: stacked ? 0 : [4, 4, 0, 0], borderColor: t.surface, borderWidth: stacked ? 1 : 0 },
    }));
  } else if (opt.type === "line" || opt.type === "area" || opt.type === "stacked_area") {
    const stackedArea = opt.type === "stacked_area";
    const p = pivot(rows, xi, si, yi, opt.additive);
    if (orderedX) {
      const order = ordered(p.xs);
      const pos = order.map((x) => p.xs.indexOf(x));
      p.series.forEach((s) => { s.data = pos.map((j) => s.data[j]); });
      p.xs = order;
    }
    const multi = p.series.length > 1;
    const endLabels = multi && p.series.length <= 4 && !stackedArea;
    option.legend = legend(t, multi);
    option.grid.right = endLabels ? 110 : 28;
    option.xAxis = categoryAxis(t, xLabels(p.xs), { boundaryGap: false });
    option.yAxis = valueAxis(t, pretty(opt.y));
    option.tooltip.trigger = "axis";
    option.tooltip.axisPointer = { type: "line", lineStyle: { color: t.axis } };
    option.series = p.series.map((s, i) => {
      const color = colorOf(t, s, i);
      return {
        name: multi ? s.name : pretty(opt.y), type: "line", data: s.data,
        symbol: "circle", symbolSize: 8, showSymbol: p.xs.length <= 40 && !stackedArea,
        stack: stackedArea ? "total" : undefined,
        lineStyle: { width: stackedArea ? 1 : 2, color }, itemStyle: { color, borderColor: t.surface, borderWidth: 2 },
        areaStyle: stackedArea ? { color, opacity: 0.55 } : multi ? undefined : { color, opacity: opt.type === "area" ? 0.3 : 0.1 },
        endLabel: { show: endLabels, formatter: "{a}", color: t.ink2 },
        emphasis: { focus: "series" },
      };
    });
    if (p.dropped.length && !opt.additive) notes.push(`Showing the ${p.series.length} largest of ${p.series.length + p.dropped.length} groups.`);
  } else if (opt.type === "heatmap") {
    const allX = ordered([...new Set(rows.map((r) => r[xi]))]), allY = ordered([...new Set(rows.map((r) => r[yi]))]);
    const xs = allX.slice(0, 40), ys = allY.slice(0, 40);
    if (allX.length > 40 || allY.length > 40) notes.push(`Showing the first ${xs.length} × ${ys.length} of ${allX.length} × ${allY.length}. The table has all of them.`);
    const xPos = new Map(xs.map((v, i) => [v, i])), yPos = new Map(ys.map((v, i) => [v, i]));
    const cells = rows.filter((r) => xPos.has(r[xi]) && yPos.has(r[yi])).map((r) => [xPos.get(r[xi]), yPos.get(r[yi]), r[vi]]);
    const vals = cells.map((c) => Number(c[2]) || 0);
    height = Math.max(260, Math.min(900, ys.length * 34 + 120));
    option.grid.top = 56;
    option.xAxis = categoryAxis(t, xLabels(xs));
    option.yAxis = categoryAxis(t, ys.map(String), { inverse: true });
    option.visualMap = {
      min: Math.min(...vals), max: Math.max(...vals), orient: "horizontal", left: 0, top: 0,
      itemWidth: 12, itemHeight: 160, calculable: false, inRange: { color: t.seq },
      textStyle: { color: t.ink3 }, formatter: compact, text: [pretty(opt.value), ""],
    };
    option.tooltip.trigger = "item";
    option.tooltip.formatter = (q) => `${escapeHtml(ys[q.value[1]])} · ${escapeHtml(xLabels([xs[q.value[0]]])[0])}<br>${escapeHtml(pretty(opt.value))}: <b>${escapeHtml(full(q.value[2]))}</b>`;
    option.series = [{ type: "heatmap", data: cells, itemStyle: { borderColor: t.surface, borderWidth: 2, borderRadius: 2 } }];
  } else if (opt.type === "scatter" || opt.type === "map" || opt.type === "bubble") {
    const isMap = opt.type === "map", sized = isMap || opt.type === "bubble";
    const pts = rows.filter((r) => typeof r[xi] === "number" && typeof r[yi] === "number");
    const sizes = sized ? pts.map((r) => Number(r[vi]) || 0) : [];
    const maxV = Math.max(1, ...sizes);
    const size = (v) => 6 + 22 * Math.sqrt(Math.max(0, v) / maxV);
    const fmt = isMap ? (v) => v.toFixed(2) : compact;
    option.grid.bottom = 32;
    option.xAxis = valueAxis(t, isMap ? "Longitude" : pretty(opt.x), { scale: true, axisLabel: { color: t.ink3, formatter: fmt } }, "x");
    option.yAxis = valueAxis(t, isMap ? "Latitude" : pretty(opt.y), { scale: true, axisLabel: { color: t.ink3, formatter: fmt } });
    option.tooltip.trigger = "item";
    option.tooltip.formatter = (q) => {
      const r = q.data.row;
      const esc = escapeHtml;
      const head = li >= 0 ? `${esc(full(r[li]))}<br>` : "";
      const sizeLine = opt.type === "bubble" ? `<br>${esc(pretty(opt.value))}: <b>${esc(full(r[vi]))}</b>` : "";
      return isMap
        ? `${head}${esc(pretty(opt.value))}: <b>${esc(full(r[vi]))}</b>`
        : `${head}${esc(pretty(opt.x))}: <b>${esc(full(r[xi]))}</b><br>${esc(pretty(opt.y))}: <b>${esc(full(r[yi]))}</b>${sizeLine}`;
    };
    option.series = [{
      type: "scatter",
      data: pts.map((r) => ({ value: [r[xi], r[yi]], row: r, symbolSize: sized ? size(Number(r[vi]) || 0) * (isMap ? 1 : 1.6) : 8 })),
      itemStyle: { color: t.series[0], opacity: sized ? 0.7 : 0.9, borderColor: t.surface, borderWidth: isMap ? 1 : 2 },
      emphasis: { itemStyle: { opacity: 1 } },
    }];
    if (isMap) notes.push(`Each dot is placed by longitude and latitude, without a base map. Dot size shows ${pretty(opt.value).toLowerCase()}.`);
    if (opt.type === "bubble") notes.push(`Bubble size shows ${pretty(opt.value).toLowerCase()}.`);
  } else if (opt.type === "pie") {
    const items = ranked(rows, xi, yi, (v) => fmtAxisValue(v, typeOf(opt.x)));
    const total = sum(items);
    const keep = items.slice(0, items.length > 9 ? 8 : 9), rest = items.slice(keep.length);
    const data = keep.map((d, i) => ({ ...d, itemStyle: { color: t.series[i % t.series.length] } }));
    if (rest.length) data.push({ name: `Other (${rest.length})`, value: sum(rest), itemStyle: { color: t.other } });
    height = 400;
    option.legend = { ...legend(t, true), type: "scroll" };
    option.tooltip.trigger = "item";
    option.tooltip.formatter = (q) => `${esc(q.name)}<br>${esc(pretty(opt.y))}: <b>${esc(full(q.value))}</b> (${pct(q.value, total)})`;
    option.series = [{
      type: "pie", radius: ["42%", "70%"], center: ["50%", "56%"], data, avoidLabelOverlap: true,
      itemStyle: { borderColor: t.surface, borderWidth: 2, borderRadius: 3 },
      label: { color: t.ink2, formatter: (q) => `${q.name}\n${q.percent}%` },
      labelLine: { lineStyle: { color: t.axis } },
    }];
    option.graphic = [{ type: "text", left: "center", top: "51%", style: { text: compact(total), fill: t.ink1, fontSize: 20, fontWeight: 600, fontFamily: FONT } },
      { type: "text", left: "center", top: "58%", style: { text: `total ${pretty(opt.y).toLowerCase()}`, fill: t.ink3, fontSize: 12, fontFamily: FONT } }];
    if (rest.length) notes.push(`The ${rest.length} smallest are joined as "Other".`);
  } else if (opt.type === "treemap") {
    const all = ranked(rows, xi, yi, (v) => fmtAxisValue(v, typeOf(opt.x)));
    const total = sum(all);
    const items = all.slice(0, 60);
    height = 420;
    option.tooltip.trigger = "item";
    option.tooltip.formatter = (q) => `${esc(q.name)}<br>${esc(pretty(opt.y))}: <b>${esc(full(q.value))}</b> (${pct(q.value, total)})`;
    option.series = [{
      type: "treemap", roam: false, nodeClick: false, breadcrumb: { show: false }, left: 0, right: 0, top: 0, bottom: 0,
      data: items.map((d, i) => ({ ...d, itemStyle: { color: t.series[i % t.series.length] } })),
      label: { show: true, color: "#fff", fontSize: 12, overflow: "truncate", formatter: (q) => `${q.name}\n${compact(q.value)} · ${pct(q.value, total)}` },
      itemStyle: { borderColor: t.surface, borderWidth: 2, gapWidth: 2 },
    }];
    if (all.length > items.length) notes.push(`Showing the largest ${items.length} of ${all.length}. The table has all of them.`);
  } else if (opt.type === "pareto") {
    const all = ranked(rows, xi, yi, (v) => fmtAxisValue(v, typeOf(opt.x)));
    const total = sum(all);
    const top = all.slice(0, MAX_BARS);
    let run = 0;
    const cum = top.map((d) => { run += d.value; return total ? Math.round((run / total) * 1000) / 10 : 0; });
    option.legend = legend(t, true);
    option.grid.top = 64;
    option.xAxis = categoryAxis(t, top.map((d) => d.name), { axisLabel: { rotate: top.length > 5 ? 30 : 0, width: 110, overflow: "truncate" } });
    option.yAxis = [valueAxis(t, pretty(opt.y)),
      valueAxis(t, "Running share", { ...shareAxis, splitLine: { show: false }, nameTextStyle: { color: t.ink3, fontSize: 12, align: "right" } })];
    option.tooltip.trigger = "axis";
    option.tooltip.axisPointer = shadow;
    option.series = [
      { name: pretty(opt.y), type: "bar", data: top.map((d) => d.value), barMaxWidth: 28, itemStyle: { color: t.series[0], borderRadius: [4, 4, 0, 0] } },
      { name: "Running share", type: "line", yAxisIndex: 1, data: cum, symbol: "circle", symbolSize: 6,
        tooltip: { valueFormatter: (v) => `${v}%` },
        lineStyle: { width: 2, color: t.series[1] }, itemStyle: { color: t.series[1], borderColor: t.surface, borderWidth: 2 } },
    ];
    const half = cum.findIndex((c) => c >= 80);
    if (half >= 0) notes.push(`The top ${half + 1} of ${all.length} make up 80% of the total.`);
    if (all.length > top.length) notes.push(`Showing the largest ${top.length}; the running share counts all ${all.length}.`);
  } else if (opt.type === "calendar") {
    const byDay = new Map();
    for (const r of rows) { const d = String(r[xi] ?? "").slice(0, 10); if (d) byDay.set(d, (byDay.get(d) || 0) + (Number(r[yi]) || 0)); }
    const days = [...byDay.keys()].sort();
    const vals = [...byDay.values()];
    const [y0, m0, d0] = days[0].split("-").map(Number), [y1, m1, d1] = days[days.length - 1].split("-").map(Number);
    const weeks = Math.round((Date.UTC(y1, m1 - 1, d1) - Date.UTC(y0, m0 - 1, d0)) / (7 * 86400000)) + 2;
    const cell = Math.max(12, Math.min(40, Math.floor(760 / weeks)));
    height = cell * 7 + 110;
    option.visualMap = {
      min: Math.min(...vals), max: Math.max(...vals), orient: "horizontal", left: 0, top: 0,
      itemWidth: 12, itemHeight: 160, calculable: false, inRange: { color: t.seq },
      textStyle: { color: t.ink3 }, formatter: compact, text: [pretty(opt.y), ""],
    };
    option.calendar = {
      top: 76, left: 36, right: 8, range: [days[0], days[days.length - 1]], cellSize: [cell, cell],
      splitLine: { lineStyle: { color: t.axis, width: 1 } }, itemStyle: { color: t.surface, borderColor: t.grid, borderWidth: 1 },
      yearLabel: { show: false }, monthLabel: { color: t.ink2, nameMap: "en" }, dayLabel: { color: t.ink3, firstDay: 1, nameMap: "en" },
    };
    option.tooltip.trigger = "item";
    option.tooltip.formatter = (q) => `${esc(fmtAxisValue(q.value[0], "Date"))}<br>${esc(pretty(opt.y))}: <b>${esc(full(q.value[1]))}</b>`;
    option.series = [{ type: "heatmap", coordinateSystem: "calendar", data: days.map((d) => [d, byDay.get(d)]) }];
    notes.push("Each square is one day; weeks start on Monday. Darker means more.");
  } else if (opt.type === "clock") {
    const byHour = new Array(24).fill(0);
    for (const r of rows) { const h = Number(r[xi]); if (h >= 0 && h < 24) byHour[h] += Number(r[yi]) || 0; }
    height = 440;
    option.polar = { radius: ["14%", "78%"], center: ["50%", "54%"] };
    option.angleAxis = {
      type: "category", data: byHour.map((_, h) => String(h).padStart(2, "0")), startAngle: 90, clockwise: true,
      axisLine: { lineStyle: { color: t.axis } }, axisTick: { show: false }, axisLabel: { color: t.ink2 }, splitLine: { show: false },
    };
    option.radiusAxis = { type: "value", axisLabel: { show: false }, axisLine: { show: false }, axisTick: { show: false }, splitLine: { lineStyle: { color: t.grid } } };
    option.tooltip.trigger = "item";
    option.tooltip.formatter = (q) => `${esc(q.name)}:00 to ${esc(String((Number(q.name) + 1) % 24).padStart(2, "0"))}:00<br>${esc(pretty(opt.y))}: <b>${esc(full(q.value))}</b>`;
    option.series = [{ name: pretty(opt.y), type: "bar", coordinateSystem: "polar", data: byHour, itemStyle: { color: t.series[0], borderRadius: 3 } }];
    notes.push("Hours run clockwise from midnight at the top, Pakistan time.");
  } else if (opt.type === "sankey" || opt.type === "sunburst") {
    const label = (v) => String(v ?? "");
    const topOf = (col, n) => new Set(ranked(rows, col, yi, label).slice(0, n).map((d) => d.name));
    const keepA = topOf(si, opt.type === "sankey" ? 12 : 10), keepB = topOf(xi, opt.type === "sankey" ? 15 : 8);
    const pairs = new Map();
    for (const r of rows) {
      const a = keepA.has(label(r[si])) ? label(r[si]) : "Other", b = keepB.has(label(r[xi])) ? label(r[xi]) : "Other";
      const key = `${a}\u0000${b}`;
      pairs.set(key, (pairs.get(key) || 0) + (Number(r[yi]) || 0));
    }
    const total = [...pairs.values()].reduce((x, v) => x + v, 0);
    if (opt.type === "sankey") {
      const left = [...new Set([...pairs.keys()].map((k) => k.split("\u0000")[0]))];
      const right = [...new Set([...pairs.keys()].map((k) => k.split("\u0000")[1]))];
      const nodes = [...left.map((n, i) => ({ name: `L\u0001${n}`, itemStyle: { color: n === "Other" ? t.other : t.series[i % t.series.length] } })),
        ...right.map((n) => ({ name: `R\u0001${n}`, itemStyle: { color: n === "Other" ? t.other : t.ink3 } }))];
      const show = (id) => String(id).split("\u0001")[1];
      height = Math.max(320, Math.min(900, Math.max(left.length, right.length) * 30 + 60));
      option.tooltip.trigger = "item";
      option.tooltip.formatter = (q) => (q.dataType === "edge"
        ? `${esc(show(q.data.source))} → ${esc(show(q.data.target))}<br>${esc(pretty(opt.y))}: <b>${esc(full(q.value))}</b> (${pct(q.value, total)})`
        : `${esc(show(q.name))}<br>${esc(pretty(opt.y))}: <b>${esc(full(q.value))}</b> (${pct(q.value, total)})`);
      option.series = [{
        type: "sankey", left: 8, right: 140, top: 8, bottom: 8, nodeWidth: 12, nodeGap: 8, draggable: false,
        emphasis: { focus: "adjacency" }, data: nodes,
        links: [...pairs].filter(([, v]) => v > 0).map(([k, v]) => { const [a, b] = k.split("\u0000"); return { source: `L\u0001${a}`, target: `R\u0001${b}`, value: v }; }),
        lineStyle: { color: "source", opacity: 0.3, curveness: 0.5 },
        label: { color: t.ink2, formatter: (q) => show(q.name) },
      }];
    } else {
      const groups = new Map();
      for (const [k, v] of pairs) {
        const [a, b] = k.split("\u0000");
        if (!groups.has(a)) groups.set(a, []);
        groups.get(a).push({ name: b, value: v });
      }
      const data = [...groups].map(([a, kids]) => ({ name: a, children: kids.sort((p, q) => q.value - p.value), value: kids.reduce((x, d) => x + d.value, 0) }))
        .sort((p, q) => (p.name === "Other") - (q.name === "Other") || q.value - p.value)
        .map((g, i) => {
          const color = g.name === "Other" ? t.other : t.series[i % t.series.length];
          return { ...g, itemStyle: { color }, children: g.children.map((k) => ({ ...k, itemStyle: { color } })) };
        });
      height = 460;
      option.tooltip.trigger = "item";
      option.tooltip.formatter = (q) => `${esc((q.treePathInfo || []).slice(1).map((n) => n.name).join(" · "))}<br>${esc(pretty(opt.y))}: <b>${esc(full(q.value))}</b> (${pct(q.value, total)})`;
      option.series = [{
        type: "sunburst", radius: ["12%", "92%"], center: ["50%", "50%"], data, sort: null, nodeClick: false,
        itemStyle: { borderColor: t.surface, borderWidth: 2 },
        label: { color: "#fff", minAngle: 12, fontSize: 11, overflow: "truncate" },
        levels: [{}, { r0: "12%", r: "50%", label: { rotate: "tangential" } }, { r0: "50%", r: "92%", itemStyle: { opacity: 0.75 }, label: { rotate: "radial" } }],
      }];
      notes.push(`Inner ring: ${pretty(opt.series).toLowerCase()}. Outer ring: ${pretty(opt.x).toLowerCase()} within each.`);
    }
    if (keepA.size < new Set(rows.map((r) => label(r[si]))).size || keepB.size < new Set(rows.map((r) => label(r[xi]))).size) {
      notes.push('Smaller groups are joined as "Other". The table has all of them.');
    }
  } else if (opt.type === "combo") {
    const p1 = pivot(rows, xi, -1, yi, true, 40), p2 = pivot(rows, xi, -1, vi, true, 40);
    option.legend = legend(t, true);
    option.grid.top = 64;
    option.xAxis = categoryAxis(t, xLabels(p1.xs), orderedX ? {} : { axisLabel: { rotate: p1.xs.length > 5 ? 30 : 0, width: 110, overflow: "truncate" } });
    option.yAxis = [valueAxis(t, pretty(opt.y)),
      valueAxis(t, pretty(opt.value), { splitLine: { show: false }, nameTextStyle: { color: t.ink3, fontSize: 12, align: "right" } })];
    option.tooltip.trigger = "axis";
    option.tooltip.axisPointer = shadow;
    option.series = [
      { name: pretty(opt.y), type: "bar", data: p1.series[0].data, barMaxWidth: 24, itemStyle: { color: t.series[0], borderRadius: [4, 4, 0, 0] } },
      { name: pretty(opt.value), type: "line", yAxisIndex: 1, data: p2.series[0].data, symbol: "circle", symbolSize: 7,
        lineStyle: { width: 2, color: t.series[1] }, itemStyle: { color: t.series[1], borderColor: t.surface, borderWidth: 2 } },
    ];
    notes.push(`Bars use the left scale (${pretty(opt.y).toLowerCase()}), the line the right scale (${pretty(opt.value).toLowerCase()}).`);
    if (p1.xTotal > p1.xs.length) notes.push(`Showing the first ${p1.xs.length} of ${p1.xTotal}. The table has all of them.`);
  } else if (opt.type === "radar") {
    const ms = (opt.measures || []).filter((m) => idx(m) >= 0);
    const mi = ms.map(idx);
    const cats = rows.slice(0, 8);
    height = 460;
    option.legend = { ...legend(t, true), type: "scroll" };
    option.radar = {
      indicator: ms.map((m, j) => ({ name: pretty(m), max: Math.max(1, ...rows.map((r) => Number(r[mi[j]]) || 0)) })),
      center: ["50%", "56%"], radius: "64%", axisName: { color: t.ink2 },
      splitLine: { lineStyle: { color: t.grid } }, splitArea: { show: false }, axisLine: { lineStyle: { color: t.grid } },
    };
    option.tooltip.trigger = "item";
    option.tooltip.formatter = (q) => `${esc(q.name)}<br>${ms.map((m, j) => `${esc(pretty(m))}: <b>${esc(full(q.value[j]))}</b>`).join("<br>")}`;
    option.series = [{
      type: "radar", symbolSize: 5,
      data: cats.map((r, i) => {
        const color = t.series[i % t.series.length];
        return { name: String(r[xi] ?? ""), value: mi.map((j) => Number(r[j]) || 0), lineStyle: { color, width: 2 }, itemStyle: { color }, areaStyle: { color, opacity: 0.08 } };
      }),
    }];
    notes.push("Each spoke runs from 0 to the largest value of that measure, so shapes compare like with like.");
  }

  box.style.height = `${height}px`;
  const instance = window.echarts.init(box, null, { renderer: "canvas" });
  instance.setOption(option);
  live.add(instance);
  return { instance, notes };
}

export function pruneCharts() {
  [...live].forEach((c) => {
    const dom = c.getDom && c.getDom();
    if (!dom || !dom.isConnected) { live.delete(c); c.dispose(); }
  });
}

export function disposeChart(instance) {
  if (!instance) return;
  live.delete(instance);
  instance.dispose();
}

window.addEventListener("resize", () => live.forEach((c) => c.resize()));
