
import { api, aiReady } from "./api.js";
import { chartSelect, disposeChart, drawChart, optionOfType, pruneCharts, tokens } from "./charts.js";
import { icon } from "./icons.js";
import { renderTable } from "./table.js";
import { onThemeChange } from "./theme.js";
import { $, $$, compact, copyRich, copyText, download, el, escapeHtml, full, highlightSql, nf, pretty, toast } from "./util.js";

const redrawers = new Set();
onThemeChange(() => [...redrawers].forEach((fn) => fn()));

export function releaseDetached() {
  [...redrawers].forEach((fn) => fn(true));
  pruneCharts();
}

function iconButton(name, label, onClick) {
  const b = el("button", { type: "button", class: "icon-btn", title: label, "aria-label": label, html: icon(name, "sm") });
  b.addEventListener("click", onClick);
  return b;
}

export function renderUser(msg, opts = {}) {
  const node = el("div", { class: "msg-user", dataset: { id: msg.id || "" } });
  let text = msg.content;

  const showView = () => {
    const bubble = el("div", { class: `bubble${msg.kind === "sql" ? " sql" : ""}`, text });
    const actions = el("div", { class: "msg-actions" },
      iconButton("copy", "Copy question", () => copyText(text)),
      msg.kind !== "sql" && opts.onEdit ? iconButton("pencil", "Edit question", showEditor) : null);
    node.replaceChildren(bubble, actions);
  };

  const showEditor = () => {
    if (!node.dataset.id) { toast("This question is still being saved. Try again in a moment."); return; }
    const box = el("textarea", { class: "edit-input", rows: 3, "aria-label": "Edit your question" });
    box.value = text;
    const save = el("button", { type: "button", class: "btn btn-sm btn-primary", text: "Save and ask" });
    const cancel = el("button", { type: "button", class: "btn btn-sm btn-ghost", text: "Cancel", onclick: showView });
    const submitEdit = async () => {
      const next = box.value.trim();
      if (next.length < 2) { toast("Type a question first."); return; }
      if (next === text) { showView(); return; }
      save.disabled = true;
      try {
        await opts.onEdit(node, next);
      } catch (e) {
        if (e.message) toast(e.message);
        save.disabled = false;
      }
    };
    save.addEventListener("click", submitEdit);
    box.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submitEdit(); }
      if (e.key === "Escape") showView();
    });
    node.replaceChildren(el("div", { class: "edit-box" }, box,
      el("div", { class: "edit-row" },
        el("span", { class: "help", text: "The answer to this question, and anything after it, will be replaced." }),
        cancel, save)));
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
  };

  showView();
  return node;
}

function answerForCopy(d, conclusion) {
  const r = d.result || { columns: [], rows: [] };
  const cols = r.columns.map((c) => pretty(c.name));
  const rows = r.rows.slice(0, 1000);
  const cell = (v) => (v === null || v === undefined ? "" : full(v));
  const lines = [d.title || "Result"];
  if (d.understood) lines.push(`Understood as: ${d.understood}`);
  (d.assumptions || []).forEach((a) => lines.push(`${a.label}: ${a.value}`));
  if (d.explanation) lines.push("", d.explanation);
  lines.push("", cols.join("\t"), ...rows.map((row) => row.map(cell).join("\t")));
  if (r.rows.length > rows.length) lines.push(`(first ${rows.length} of ${r.rows.length} rows; download the CSV for all)`);
  if (conclusion) {
    lines.push("", `Conclusion: ${conclusion.headline}`, ...(conclusion.findings || []).map((f) => `- ${f}`));
    if ((conclusion.caveats || []).length) lines.push("Keep in mind:", ...conclusion.caveats.map((f) => `- ${f}`));
  }
  const text = lines.join("\n");

  const e = escapeHtml;
  const html = [
    `<h3>${e(d.title || "Result")}</h3>`,
    d.understood ? `<p><b>Understood as:</b> ${e(d.understood)}</p>` : "",
    (d.assumptions || []).length ? `<p>${d.assumptions.map((a) => `<b>${e(a.label)}:</b> ${e(a.value)}`).join("<br>")}</p>` : "",
    d.explanation ? `<p>${e(d.explanation)}</p>` : "",
    `<table border="1" cellspacing="0" cellpadding="4"><thead><tr>${cols.map((c) => `<th>${e(c)}</th>`).join("")}</tr></thead><tbody>`,
    rows.map((row) => `<tr>${row.map((v) => `<td>${e(cell(v))}</td>`).join("")}</tr>`).join(""),
    "</tbody></table>",
    conclusion ? `<p><b>${e(conclusion.headline)}</b></p><ul>${(conclusion.findings || []).map((f) => `<li>${e(f)}</li>`).join("")}</ul>` : "",
    conclusion && (conclusion.caveats || []).length ? `<p>Keep in mind:</p><ul>${conclusion.caveats.map((f) => `<li>${e(f)}</li>`).join("")}</ul>` : "",
  ].join("");
  return { text, html };
}

function meta(parts, extra) {
  const row = el("div", { class: "answer-meta" });
  parts.filter(Boolean).forEach((p, i) => {
    if (i) row.append(el("span", { class: "sep", text: "·" }));
    row.append(el("span", { text: p }));
  });
  if (extra) row.append(extra);
  return row;
}

function stepsBlock(steps) {
  if (!steps || !steps.length) return { toggle: null, list: null };
  const list = el("ol", { class: "steps", hidden: true }, steps.map((s) => el("li", { text: s })));
  const toggle = el("button", { type: "button", class: "link steps-toggle", "aria-expanded": "false", text: "How it was answered" });
  toggle.addEventListener("click", () => {
    const open = list.hidden;
    list.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    toggle.textContent = open ? "Hide steps" : "How it was answered";
  });
  return { toggle, list };
}

function renderReply(msg) {
  const d = msg.data || {};
  const kind = msg.kind;
  const label = { clarify: "Needs more detail", cannot_answer: "Not answerable from this data", error: d.stopped ? "Stopped" : "Something went wrong" }[kind] || "Reply";
  const { toggle, list } = stepsBlock(d.steps);
  return el("article", { class: `reply ${kind === "clarify" ? "clarify" : kind === "error" && !d.stopped ? "error" : ""}`, dataset: { id: msg.id } },
    meta([label, d.seconds ? `${d.seconds} s` : ""], toggle),
    list,
    understoodLine(d.understood),
    d.title && kind !== "error" ? el("h3", { class: "reply-title", text: d.title }) : null,
    el("p", { class: "reply-text", text: msg.content }),
    el("div", { class: "reply-actions" }, iconButton("copy", "Copy reply", () => copyText(
      [d.understood ? `Understood as: ${d.understood}` : "", msg.content].filter(Boolean).join(String.fromCharCode(10))))),
    kind === "clarify" ? el("p", { class: "reply-hint", text: "Answer in the box below. Your earlier question stays in context." }) : null);
}

function understoodLine(text) {
  if (!text) return null;
  return el("p", { class: "understood" }, el("span", { class: "understood-label", text: "Understood as" }), el("span", { text }));
}

function actionButton(iconName, label, extra = {}) {
  const b = el("button", { type: "button", class: "btn btn-sm", ...extra });
  b.innerHTML = `${icon(iconName, "sm")}<span>${label}</span>`;
  return b;
}

export function renderAssistant(msg) {
  if (msg.kind !== "result") return renderReply(msg);
  const d = msg.data;
  const r = d.result;
  const options = (d.charts && d.charts.options) || [];
  const canChart = options.length > 0 && r.row_count > 0;
  const { toggle, list } = stepsBlock(d.steps);

  const stats = [`${nf.format(r.row_count)} ${r.row_count === 1 ? "row" : "rows"}`];
  if (r.rows_read) stats.push(`${compact(r.rows_read)} records checked`);
  if (r.truncated) stats.push(`first ${nf.format(r.row_count)} shown`);
  const csvBtn = el("button", { type: "button", class: "icon-btn", title: "Download the table (CSV)", "aria-label": "Download the table as CSV", html: icon("download") });
  csvBtn.addEventListener("click", () => download(`/api/messages/${msg.id}/export.csv`, "result.csv"));
  const tableWrap = el("div", { class: "table-wrap" });
  const tableNote = el("p", { class: "note", hidden: true });
  renderTable(tableWrap, tableNote, r);

  const chartSection = el("section", { class: "chart-section", hidden: true });
  const select = chartSelect(options);
  select.value = String(d.chart_choice ?? (d.charts && d.charts.default) ?? 0);
  const chartBox = el("div", { class: "chart" });
  const chartNote = el("p", { class: "note", hidden: true });
  const pngBtn = el("button", { type: "button", class: "icon-btn", title: "Download the chart (PNG)", "aria-label": "Download the chart as PNG", html: icon("image") });
  const hideBtn = el("button", { type: "button", class: "btn btn-sm btn-ghost", text: "Hide chart" });
  chartSection.append(
    el("div", { class: "chart-head" }, el("h4", { text: "Chart" }),
      options.length > 1 ? el("label", { class: "chart-picker" }, el("span", { class: "visually-hidden", text: "Chart type" }), select) : null,
      el("span", { class: "spacer" }), pngBtn, hideBtn),
    chartBox, chartNote);

  let chart = null;
  const draw = () => {
    if (chartSection.hidden || !chartBox.isConnected) return;
    disposeChart(chart);
    const out = drawChart(chartBox, r, options[Number(select.value) || 0]);
    chart = out.instance;
    chartNote.textContent = out.notes.join(" ");
    chartNote.hidden = !out.notes.length;
    pngBtn.hidden = !chart;
  };
  const redraw = (releaseOnly = false) => {
    if (chartBox.isConnected) { if (!releaseOnly) draw(); return; }
    disposeChart(chart);
    chart = null;
    redrawers.delete(redraw);
  };
  redrawers.add(redraw);
  select.addEventListener("change", () => { draw(); api.chooseChart(msg.id, Number(select.value)).catch(() => {}); });
  pngBtn.addEventListener("click", () => {
    if (chart) download(chart.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: tokens().surface }), `${(d.title || "chart").replace(/[^\w]+/g, "_")}.png`);
  });

  const chartBtn = actionButton("chart", "Draw chart", canChart ? {} : { disabled: true, title: "No chart fits this result" });
  const showChart = (remember) => {
    chartSection.hidden = false;
    chartBtn.hidden = true;
    requestAnimationFrame(draw);
    if (remember) {
      api.chooseChart(msg.id, Number(select.value)).catch(() => {});
      api.setView(msg.id, { chart_hidden: false }).catch(() => {});
    }
  };
  chartBtn.addEventListener("click", () => showChart(true));
  hideBtn.addEventListener("click", () => {
    chartSection.hidden = true;
    chartBtn.hidden = false;
    disposeChart(chart);
    chart = null;
    api.setView(msg.id, { chart_hidden: true }).catch(() => {});
    chartBtn.focus();
  });

  const conclusion = el("section", { class: "conclusion", hidden: true });
  const concludeBtn = actionButton("text", "Write conclusion", r.row_count ? {} : { disabled: true, title: "There is nothing to conclude from an empty result" });
  let shownConclusion = d.conclusion || null;
  const setConcludeLabel = (label) => { concludeBtn.innerHTML = `${icon("text", "sm")}<span>${label}</span>`; };
  const hideConclusion = (remember) => {
    conclusion.hidden = true;
    concludeBtn.hidden = false;
    concludeBtn.disabled = false;
    setConcludeLabel("Show conclusion");
    if (remember) {
      api.setView(msg.id, { conclusion_hidden: true }).catch(() => {});
      concludeBtn.focus();
    }
  };
  const showConclusion = (c, remember = false) => {
    shownConclusion = c;
    const hideConcl = el("button", { type: "button", class: "btn btn-sm btn-ghost", text: "Hide conclusion", onclick: () => hideConclusion(true) });
    conclusion.replaceChildren(
      el("div", { class: "conclusion-head" }, el("span", { class: "conclusion-label", text: "Conclusion" }), el("span", { class: "spacer" }), hideConcl),
      el("h4", { text: c.headline }),
      c.findings && c.findings.length ? el("ul", {}, c.findings.map((f) => el("li", { text: f }))) : null,
      c.caveats && c.caveats.length ? el("div", { class: "caveats-title", text: "Keep in mind" }) : null,
      c.caveats && c.caveats.length ? el("ul", { class: "caveats" }, c.caveats.map((f) => el("li", { text: f }))) : null,
      c.unverified && c.unverified.length
        ? el("p", { class: "unverified", text: `Could not check these figures against the table: ${c.unverified.join(", ")}. Treat them with care.` })
        : null,
      el("p", { class: "source", text: c.unverified && c.unverified.length
        ? "Written by the AI from the result above."
        : "Written by the AI from the result above. Every figure was checked against the table." }));
    conclusion.hidden = false;
    concludeBtn.hidden = true;
    if (remember) api.setView(msg.id, { conclusion_hidden: false }).catch(() => {});
  };
  concludeBtn.addEventListener("click", async () => {
    if (shownConclusion) {
      showConclusion(shownConclusion, true);
      return;
    }
    if (!aiReady()) { toast("Add an AI provider and key in Settings first."); location.hash = "#/settings"; return; }
    concludeBtn.disabled = true;
    setConcludeLabel("Writing…");
    try {
      showConclusion(await api.conclude(msg.id));
    } catch (e) {
      toast(e.message);
      concludeBtn.disabled = false;
      setConcludeLabel("Try again");
    }
  });

  const verifyBtn = el("button", { type: "button", class: "btn btn-sm btn-ghost", title: "Confirm the AI understood and answered this correctly. It will follow the same approach for similar questions." });
  const setVerified = () => {
    verifyBtn.innerHTML = `${icon("check", "sm")}<span>Marked as correct</span>`;
    verifyBtn.classList.add("done");
    verifyBtn.disabled = true;
  };
  if (d.verified) setVerified();
  else verifyBtn.innerHTML = `${icon("check", "sm")}<span>Mark as correct</span>`;
  verifyBtn.addEventListener("click", async () => {
    verifyBtn.disabled = true;
    try { await api.verify(msg.id); setVerified(); toast("Thanks. The AI will follow this approach for similar questions."); }
    catch (e) { verifyBtn.disabled = false; toast(e.message); }
  });

  const sqlPre = el("pre", { class: "sql-view" });
  sqlPre.append(highlightSql(d.sql));
  const copyBtn = actionButton("copy", "Copy", { class: "btn btn-sm btn-ghost" });
  copyBtn.addEventListener("click", () => copyText(d.sql));
  const queryBox = el("details", { class: "query-used" },
    el("summary", { text: "Query used" }),
    el("p", { class: "help", text: "Written and run automatically to answer the question." }),
    sqlPre, el("div", { class: "sql-actions" }, copyBtn));

  const copyAnswerBtn = actionButton("copy", "Copy answer", { title: "Copy the whole answer: title, assumptions, table and conclusion" });
  copyAnswerBtn.addEventListener("click", () => {
    const { text, html } = answerForCopy(d, shownConclusion);
    copyRich(text, html);
  });

  const act = (action) => {
    if (action === "chart" || action.startsWith("chart:")) {
      if (!canChart) return "No chart fits this result.";
      const want = action.split(":")[1];
      if (want) {
        const i = optionOfType(options, want);
        if (i < 0) {
          if (chartSection.hidden) showChart(true);
          return "";
        }
        select.value = String(i);
        if (chartSection.hidden) showChart(true); else { draw(); api.chooseChart(msg.id, i).catch(() => {}); }
        return "";
      }
      if (!chartSection.hidden) return "";
      showChart(true); return "Chart drawn.";
    }
    if (action === "hide_chart") {
      if (chartSection.hidden) return "";
      hideBtn.click(); return "Chart hidden.";
    }
    if (action === "conclusion") {
      if (!conclusion.hidden) return "";
      if (concludeBtn.disabled) return "There is nothing to conclude from this result.";
      concludeBtn.click(); return shownConclusion ? "Conclusion shown." : "Writing the conclusion.";
    }
    if (action === "hide_conclusion") {
      if (conclusion.hidden) return "";
      hideConclusion(true); return "Conclusion hidden.";
    }
    if (action === "export") { csvBtn.click(); return "Downloading the table as CSV."; }
    return "";
  };

  const card = el("article", { class: "answer", dataset: { id: msg.id } },
    meta(["Answer", d.seconds ? `${d.seconds} s` : ""], toggle),
    list,
    understoodLine(d.understood),
    el("h3", { text: d.title || "Result" }),
    d.assumptions && d.assumptions.length
      ? el("div", { class: "chips" }, d.assumptions.map((a) => el("span", { class: "chip" }, el("b", { text: a.label }), el("span", { text: a.value }))))
      : null,
    d.explanation ? el("p", { class: "explanation", text: d.explanation }) : null,
    r.row_count === 0
      ? el("p", { class: "callout", style: "margin-top:12px", text: "No records matched. Try a wider period or check the names in the question." })
      : el("section", { class: "result" },
          el("div", { class: "result-bar" }, el("span", { class: "result-stats", text: stats.join(" · ") }), csvBtn),
          tableWrap, tableNote),
    chartSection,
    conclusion,
    el("div", { class: "answer-actions" }, chartBtn, concludeBtn, copyAnswerBtn, el("span", { class: "spacer" }), verifyBtn),
    queryBox);

  if (d.conclusion) {
    showConclusion(d.conclusion);
    if (d.conclusion_hidden) hideConclusion(false);
  }
  if (canChart && d.chart_choice !== undefined && d.chart_choice !== null && !d.chart_hidden) showChart(false);
  card._act = act;
  return card;
}

export function renderPending() {
  const list = el("ol", { class: "steps" });
  const timer = el("span", { class: "timer", text: "0 s" });
  const node = el("article", { class: "answer pending" }, meta(["Working on it"], timer), list);
  const started = performance.now();
  const id = setInterval(() => { timer.textContent = `${Math.round((performance.now() - started) / 1000)} s`; }, 1000);
  return {
    node,
    step(text) {
      const prev = $("li.now", list);
      if (prev && prev.textContent === text) return;
      if (prev) prev.classList.remove("now");
      list.append(el("li", { class: "now", text }));
    },
    done() { clearInterval(id); $$("li.now", list).forEach((li) => li.classList.remove("now")); node.remove(); },
  };
}
