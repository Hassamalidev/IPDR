
import { api } from "./api.js";
import { icon } from "./icons.js";
import { $, debounce, el, fmtDateTime, nf, toast } from "./util.js";

const ACCEPT = ".csv,.tsv,.xlsx,.xlsm,.json,.yaml,.yml,.md,.markdown,.txt";

export async function renderDictionary(view) {
  view.replaceChildren(el("div", { class: "page-inner" }, el("p", { class: "muted", text: "Loading…" })));
  let data;
  try {
    data = await api.dictionary();
  } catch (e) {
    view.replaceChildren(el("div", { class: "page-inner" }, el("p", { class: "callout bad", text: e.message })));
    return;
  }
  const s = data.summary;

  const fileInput = el("input", { type: "file", accept: ACCEPT, hidden: true });
  const fileName = el("span", { class: "file-name" });
  const zone = el("div", { class: "dropzone", tabindex: "0", role: "button", "aria-label": "Choose a dictionary file" },
    el("span", { html: icon("upload") }),
    el("span", {}, el("strong", { text: "Drop a file here" }), " or click to choose one"),
    el("span", { class: "muted", text: "CSV, Excel, JSON, YAML, Markdown or text. One row per field: field, description, and optionally type, example, values, notes." }),
    fileName);
  const nameInput = el("input", { class: "input", placeholder: "Name (optional), e.g. Vendor field guide v2", "aria-label": "Name for this file" });
  const uploadBtn = el("button", { type: "button", class: "btn btn-primary", disabled: true });
  uploadBtn.innerHTML = `${icon("upload", "sm")}<span>Upload</span>`;
  const report = el("div", { class: "report", role: "status" });
  let chosen = null;

  const choose = (f) => {
    chosen = f;
    fileName.textContent = f ? `${f.name} (${nf.format(Math.ceil(f.size / 1024))} KB)` : "";
    uploadBtn.disabled = !f;
  };
  zone.addEventListener("click", () => fileInput.click());
  zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
  fileInput.addEventListener("change", () => choose(fileInput.files[0] || null));
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("over"); choose(e.dataTransfer.files[0] || null); });
  uploadBtn.addEventListener("click", async () => {
    if (!chosen) return;
    uploadBtn.disabled = true;
    report.replaceChildren(el("span", { class: "muted", text: "Reading the file…" }));
    try {
      const d = await api.uploadDictionary(chosen, nameInput.value.trim());
      const r = d.report || {};
      toast(`Added ${d.name}`);
      await renderDictionary(view);
      const rep = $(".report", view);
      rep.replaceChildren(...reportLines(d.name, r));
    } catch (e) {
      report.replaceChildren(el("p", { class: "callout bad", text: e.message }));
      uploadBtn.disabled = false;
    }
  });

  const files = data.files.length
    ? el("div", { class: "list-wrap" }, el("table", { class: "list" },
        el("thead", {}, el("tr", {}, ["Name", "File", "Added", "Fields", "Matched", "Used by the AI", ""].map((h) => el("th", { text: h })))),
        el("tbody", {}, data.files.map((f) => {
          const sw = el("input", { type: "checkbox", "aria-label": `Use ${f.name}` });
          sw.checked = f.active;
          sw.addEventListener("change", async () => {
            try { await api.toggleDictionary(f.id, sw.checked); await renderDictionary(view); }
            catch (e) { toast(e.message); sw.checked = !sw.checked; }
          });
          const del = el("button", { type: "button", class: "icon-btn", title: "Delete", "aria-label": `Delete ${f.name}`, html: icon("trash", "sm") });
          del.addEventListener("click", async () => {
            if (!confirm(`Delete "${f.name}"? The AI will stop using its descriptions.`)) return;
            try { await api.deleteDictionary(f.id); await renderDictionary(view); } catch (e) { toast(e.message); }
          });
          const r = f.report || {};
          return el("tr", {},
            el("td", { text: f.name }),
            el("td", { class: "muted", text: f.filename }),
            el("td", { class: "muted", text: fmtDateTime(f.created_at) }),
            el("td", { text: nf.format(f.fields.length) }),
            el("td", {}, el("span", { text: `${r.matched ?? 0} of ${r.fields ?? f.fields.length}` }),
              r.unknown && r.unknown.length ? el("span", { class: "badge warn", style: "margin-left:6px", title: r.unknown.join(", "), text: `${r.unknown.length} not in table` }) : null),
            el("td", {}, el("label", { class: "switch" }, sw, el("span"))),
            el("td", {}, del));
        }))))
    : el("p", { class: "muted", text: "No files uploaded yet. The AI uses the built-in descriptions." });

  const search = el("input", { type: "search", placeholder: "Filter fields" });
  const onlyMissing = el("input", { type: "checkbox" });
  const tbody = el("tbody");
  const drawFields = () => {
    const q = search.value.trim().toLowerCase();
    tbody.replaceChildren(...data.columns
      .filter((c) => !q || c.name.toLowerCase().includes(q) || (c.description || "").toLowerCase().includes(q))
      .filter((c) => !onlyMissing.checked || (!c.description && !c.empty))
      .map((c) => el("tr", { class: c.empty ? "empty-col" : "" },
        el("td", {}, el("code", { text: c.name })),
        el("td", { class: "muted mono", style: "font-size:12px", text: c.type }),
        el("td", { class: "desc" },
          c.empty && !c.description ? el("span", { class: "muted", text: "Always empty. The AI is told not to use it." })
            : c.description ? c.description : el("span", { class: "missing", text: "No description. The AI has to guess what this means." }),
          c.values ? el("div", { class: "muted", style: "font-size:12.5px;margin-top:2px", text: `Values: ${c.values}` }) : null,
          c.example ? el("div", { class: "muted", style: "font-size:12.5px", text: `Example: ${c.example}` }) : null),
        el("td", {}, c.source ? el("span", { class: `badge${c.source === "built-in" ? "" : " accent"}`, text: c.source }) : null))));
  };
  search.addEventListener("input", debounce(drawFields, 120));
  onlyMissing.addEventListener("change", drawFields);
  drawFields();

  const promptBox = el("details", {},
    el("summary", { text: `See exactly what the AI reads (${nf.format(s.prompt_chars)} characters)` }));
  promptBox.addEventListener("toggle", async () => {
    if (!promptBox.open || promptBox.querySelector("pre")) return;
    const pre = el("pre", { class: "prompt-view", text: "Loading…" });
    promptBox.append(pre);
    try { pre.textContent = await api.dictionaryPrompt(); } catch (e) { pre.textContent = e.message; }
  });

  const template = el("a", { class: "btn", href: "/api/dictionary/template.csv", download: "data_dictionary_template.csv" });
  template.innerHTML = `${icon("download", "sm")}<span>Download template</span>`;

  view.replaceChildren(el("div", { class: "page-inner" },
    el("div", { class: "page-head" },
      el("div", {}, el("h1", { text: "Data dictionary" }),
        el("p", { text: "The AI understands the data only through these descriptions. Clear, specific descriptions of each field, its units and its codes are the biggest single improvement to answer accuracy." })),
      el("div", { class: "actions" }, template)),

    el("section", { class: "panel" },
      el("div", { class: "stats-row" },
        stat(nf.format(s.columns), "fields in the table"),
        stat(nf.format(s.described), "described"),
        stat(nf.format(s.empty), "always empty"),
        stat(nf.format(s.missing.length), "missing a description"))),

    el("section", { class: "panel" },
      el("div", { class: "panel-head" }, el("div", { class: "grow" },
        el("h2", { text: "Upload field descriptions" }),
        el("p", { text: "The template holds every field with its current description. Fill it in or correct it in Excel, then upload it here. Later uploads override earlier ones; fields not in the table are reported and ignored." }))),
      zone, fileInput,
      el("div", { class: "form-actions" }, el("div", { class: "grow", style: "flex:1;min-width:220px" }, nameInput), uploadBtn),
      report),

    el("section", { class: "panel" },
      el("div", { class: "panel-head" }, el("div", { class: "grow" }, el("h2", { text: "Uploaded files" }))),
      files,
      data.notes.length ? el("p", { class: "help", style: "margin-top:10px", text: `Free-text notes from ${data.notes.map((n) => n.name).join(", ")} are also given to the AI.` }) : null),

    el("section", { class: "panel" },
      el("div", { class: "panel-head" }, el("div", { class: "grow" },
        el("h2", { text: "Fields" }),
        el("p", { text: "What the AI is told about each field, and where that description comes from." }))),
      el("div", { class: "list-tools" }, el("label", { class: "search" }, search),
        el("label", { class: "check" }, onlyMissing, "Only fields without a description")),
      el("div", { class: "list-wrap" }, el("table", { class: "list" },
        el("thead", {}, el("tr", {}, ["Field", "Type", "Description", "Source"].map((h) => el("th", { text: h })))), tbody)),
      el("div", { style: "margin-top:14px" }, promptBox))));
}

function stat(value, label) {
  return el("div", { class: "stat" }, el("b", { text: value }), el("span", { text: label }));
}

function reportLines(name, r) {
  const out = [el("p", { class: "status-line ok", text: `${name}: ${r.matched ?? 0} of ${r.fields ?? 0} fields match the table and are now used by the AI.` })];
  if (r.unknown && r.unknown.length) {
    out.push(el("p", { class: "status-line", text: `Not in the table (ignored): ${r.unknown.slice(0, 30).join(", ")}${r.unknown.length > 30 ? "…" : ""}` }));
  }
  if (r.no_description && r.no_description.length) {
    out.push(el("p", { class: "status-line", text: `Listed without a description: ${r.no_description.slice(0, 30).join(", ")}` }));
  }
  if (r.notes_truncated) out.push(el("p", { class: "status-line", text: "The free text was long and has been shortened." }));
  return out;
}
