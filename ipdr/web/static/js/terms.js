
import { get, send } from "./api.js";
import { icon } from "./icons.js";
import { debounce, el, nf, toast } from "./util.js";

let list = [];

export async function renderTerms(view) {
  view.replaceChildren(el("div", { class: "page-inner" }, el("p", { class: "muted", text: "Loading…" })));
  try {
    list = await get("/api/terms");
  } catch (e) {
    view.replaceChildren(el("div", { class: "page-inner" }, el("p", { class: "callout bad", text: e.message })));
    return;
  }

  const term = el("input", { class: "input", placeholder: "e.g. heavy users, KHI, peak hours, Jazz", "aria-label": "Term" });
  const meaning = el("textarea", { class: "input", rows: 2, placeholder: "e.g. Subscribers who used more than 1 GB in a day",
    "aria-label": "What it means", style: "height:auto;padding:8px 10px" });
  const status = el("p", { class: "status-line", role: "status" });
  const addBtn = el("button", { type: "button", class: "btn btn-primary" });
  addBtn.innerHTML = `${icon("plus", "sm")}<span>Add term</span>`;
  addBtn.addEventListener("click", async () => {
    if (!term.value.trim() || !meaning.value.trim()) {
      status.textContent = "Fill in both the term and what it means.";
      status.className = "status-line bad";
      return;
    }
    addBtn.disabled = true;
    try {
      await send("POST", "/api/terms", { term: term.value.trim(), meaning: meaning.value.trim() });
      toast("Added. The AI uses it from the next question.");
      await renderTerms(view);
    } catch (e) {
      status.textContent = e.message;
      status.className = "status-line bad";
      addBtn.disabled = false;
    }
  });
  meaning.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) addBtn.click(); });

  const search = el("input", { type: "search", placeholder: "Filter terms", "aria-label": "Filter terms" });
  const tbody = el("tbody");
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    const shown = list.filter((t) => !q || t.term.toLowerCase().includes(q) || t.meaning.toLowerCase().includes(q));
    tbody.replaceChildren(...(shown.length
      ? shown.map((t) => row(t, view))
      : [el("tr", {}, el("td", { colspan: "3", class: "muted", text: list.length ? "No terms match." : "No terms yet." }))]));
  };
  search.addEventListener("input", debounce(draw, 120));
  draw();

  view.replaceChildren(el("div", { class: "page-inner" },
    el("div", { class: "page-head" }, el("div", {},
      el("h1", { text: "Terms" }),
      el("p", { text: "People ask in their own words: short forms, local names, team jargon, Urdu or English. Tell the AI what your words mean in the data, in plain language. It reads this list with every question, so a new term works straight away." }))),

    el("section", { class: "panel" },
      el("div", { class: "panel-head" }, el("div", { class: "grow" },
        el("h2", { text: "Add a term" }),
        el("p", { text: "Several words that mean the same thing can share one entry, separated by commas." }))),
      el("div", { class: "form-grid" },
        el("label", { class: "field" }, el("span", { text: "Term" }), term),
        el("label", { class: "field" }, el("span", { text: "What it means" }), meaning)),
      el("div", { class: "form-actions" }, addBtn, status)),

    el("section", { class: "panel" },
      el("div", { class: "list-tools" },
        el("label", { class: "search" }, search),
        el("span", { class: "help", text: `${nf.format(list.length)} terms` })),
      el("div", { class: "list-wrap" }, el("table", { class: "list terms-table" },
        el("thead", {}, el("tr", {}, el("th", { text: "Term" }), el("th", { text: "What it means" }), el("th", { text: "" }))),
        tbody))),

    el("section", { class: "panel" },
      el("div", { class: "panel-head" }, el("div", { class: "grow" },
        el("h2", { text: "How the AI handles any question" }))),
      el("ol", { class: "how-list" },
        el("li", {}, el("b", { text: "Understands it first. " }), "Fixes spelling, expands short forms, translates Urdu and Roman Urdu, uses these terms, turns “last week” into real dates, and fills gaps with sensible defaults. Each answer shows the question as it was understood."),
        el("li", {}, el("b", { text: "Checks names against the data. " }), "Apps, cities and areas are looked up so the spelling matches the records."),
        el("li", {}, el("b", { text: "Writes, tests and runs the query, " }), "and fixes it if the test fails or the result looks wrong."),
        el("li", {}, el("b", { text: "Learns from confirmed answers. " }), "When you click “Mark as correct” under an answer, similar questions later follow the same approach.")))));
}

function row(t, view) {
  const tr = el("tr", { dataset: { id: t.id } });
  const showView = () => {
    const edit = el("button", { type: "button", class: "icon-btn", title: "Edit", "aria-label": `Edit ${t.term}`, html: icon("pencil", "sm") });
    const del = el("button", { type: "button", class: "icon-btn", title: "Delete", "aria-label": `Delete ${t.term}`, html: icon("trash", "sm") });
    edit.addEventListener("click", showEdit);
    del.addEventListener("click", async () => {
      if (!confirm(`Delete “${t.term}”? The AI will stop using it.`)) return;
      try { await send("DELETE", `/api/terms/${t.id}`); toast("Deleted"); await renderTerms(view); } catch (e) { toast(e.message); }
    });
    tr.replaceChildren(
      el("td", { class: "term-cell", text: t.term }),
      el("td", { class: "desc", text: t.meaning }),
      el("td", { class: "row-actions" }, edit, del));
  };
  const showEdit = () => {
    const termIn = el("input", { class: "input", value: t.term, "aria-label": "Term" });
    const meaningIn = el("textarea", { class: "input", rows: 2, "aria-label": "What it means", style: "height:auto;padding:6px 8px;width:100%" });
    meaningIn.value = t.meaning;
    const save = el("button", { type: "button", class: "btn btn-sm btn-primary", text: "Save" });
    const cancel = el("button", { type: "button", class: "btn btn-sm btn-ghost", text: "Cancel", onclick: showView });
    save.addEventListener("click", async () => {
      if (!termIn.value.trim() || !meaningIn.value.trim()) { toast("Fill in both fields."); return; }
      try {
        await send("PATCH", `/api/terms/${t.id}`, { term: termIn.value.trim(), meaning: meaningIn.value.trim() });
        t.term = termIn.value.trim();
        t.meaning = meaningIn.value.trim();
        toast("Saved");
        showView();
      } catch (e) { toast(e.message); }
    });
    tr.replaceChildren(el("td", {}, termIn), el("td", {}, meaningIn), el("td", { class: "row-actions" }, save, cancel));
    termIn.focus();
  };
  showView();
  return tr;
}
