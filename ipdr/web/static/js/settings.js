
import { api, forgetKey, getKey, getLLM, saveLLM, setKey, state } from "./api.js";
import { BrowserSR, checkMicrophone, getVoice, LANGUAGES, saveVoice, voiceKeyName, VoiceInput } from "./voice.js";
import { icon } from "./icons.js";
import { getTheme, setTheme } from "./theme.js";
import { $, el, fmtDay, nf, toast } from "./util.js";

let onSaved = (_quiet) => {};
export function onSettingsSaved(fn) { onSaved = fn; }

export function renderSettings(view) {
  const meta = state.meta || {};
  const presets = meta.providers || {};
  const defaults = meta.defaults || {};
  const cfg = getLLM();

  const provider = el("select", { id: "s-provider" }, Object.entries(presets).map(([k, p]) => el("option", { value: k, text: p.label })));
  provider.value = presets[cfg.provider] ? cfg.provider : Object.keys(presets)[0];
  const baseUrl = el("input", { id: "s-base", type: "url", placeholder: "https://…/v1", spellcheck: "false" });
  const key = el("input", { id: "s-key", type: "password", autocomplete: "off", spellcheck: "false" });
  const showKey = el("button", { type: "button", class: "icon-btn", title: "Show key", "aria-label": "Show key", html: icon("eye") });
  const remember = el("input", { type: "checkbox", id: "s-remember" });
  const model = el("input", { id: "s-model", list: "s-models", spellcheck: "false", placeholder: "Model name" });
  const modelList = el("datalist", { id: "s-models" });
  const loadModels = el("button", { type: "button", class: "btn btn-sm" });
  loadModels.innerHTML = `${icon("refresh", "sm")}<span>Load models</span>`;
  const effort = el("select", { id: "s-effort" },
    el("option", { value: "low", text: "Fast: least thinking, lowest cost" }),
    el("option", { value: "medium", text: "Balanced (recommended)" }),
    el("option", { value: "high", text: "Thorough: best for hard questions" }));
  const status = el("p", { class: "status-line", role: "status" });
  const keyHelp = el("small");
  const baseField = el("label", { class: "field full" }, el("span", { text: "Base URL" }), baseUrl,
    el("small", { text: "The provider's OpenAI-compatible endpoint, ending in /v1 or similar." }));
  const effortField = el("label", { class: "field" }, el("span", { text: "Answer quality" }), effort,
    el("small", { text: "How long the model may think before answering. Claude only." }));
  const saveBtn = el("button", { type: "button", class: "btn btn-primary", text: "Save" });
  const testBtn = el("button", { type: "button", class: "btn", text: "Test connection" });
  const forgetBtn = el("button", { type: "button", class: "btn btn-ghost btn-danger", text: "Forget key" });

  const fill = (p, keepValues) => {
    const preset = presets[p] || {};
    const saved = getLLM();
    const same = saved.provider === p;
    baseField.hidden = preset.kind !== "openai";
    effortField.hidden = preset.kind !== "anthropic";
    baseUrl.placeholder = preset.base_url || "https://…/v1";
    if (!keepValues) {
      baseUrl.value = same ? saved.base_url : "";
      key.value = getKey(p);
      model.value = same ? saved.model : (p === defaults.provider ? defaults.model : preset.default_model || "");
      effort.value = same ? saved.effort : "medium";
      remember.checked = same ? saved.remember : true;
    }
    key.placeholder = preset.key_hint || "";
    modelList.replaceChildren(...(preset.models || []).map((m) => el("option", { value: m })));
    const serverKey = defaults.server_has_key && defaults.provider === p;
    keyHelp.textContent = !preset.needs_key
      ? "Usually not needed for this provider."
      : serverKey
        ? "Leave empty to use the key configured on the server."
        : "Required. Stored only in this browser and sent with each request; the server does not save it.";
    status.textContent = "";
    status.className = "status-line";
  };
  fill(provider.value, false);

  const current = () => ({ provider: provider.value, model: model.value.trim(), base_url: baseUrl.value.trim(), effort: effort.value, remember: remember.checked });

  provider.addEventListener("change", () => fill(provider.value, false));
  showKey.addEventListener("click", () => {
    const show = key.type === "password";
    key.type = show ? "text" : "password";
    showKey.innerHTML = icon(show ? "eyeOff" : "eye");
    showKey.title = show ? "Hide key" : "Show key";
  });
  saveBtn.addEventListener("click", () => {
    const c = current();
    if (!c.model) { status.textContent = "Enter a model name."; status.className = "status-line bad"; return; }
    saveLLM(c, key.value.trim());
    status.textContent = "Saved.";
    status.className = "status-line ok";
    onSaved();
  });
  forgetBtn.addEventListener("click", () => {
    forgetKey(provider.value);
    key.value = "";
    status.textContent = "Key removed from this browser.";
    status.className = "status-line";
    onSaved();
  });
  testBtn.addEventListener("click", async () => {
    testBtn.disabled = true;
    status.textContent = "Testing…";
    status.className = "status-line";
    try {
      const out = await api.testLLM(current(), key.value.trim());
      status.textContent = out.message;
      status.className = "status-line ok";
    } catch (e) {
      status.textContent = e.message;
      status.className = "status-line bad";
    } finally {
      testBtn.disabled = false;
    }
  });
  loadModels.addEventListener("click", async () => {
    loadModels.disabled = true;
    try {
      const out = await api.listModels(current(), key.value.trim());
      modelList.replaceChildren(...out.models.map((m) => el("option", { value: m })));
      status.textContent = out.models.length ? `${out.models.length} models available. Start typing in the model field to pick one.` : "The provider returned no models.";
      status.className = "status-line ok";
      model.focus();
    } catch (e) {
      status.textContent = e.message;
      status.className = "status-line bad";
    } finally {
      loadModels.disabled = false;
    }
  });

  const voicePanel = voiceSettings();

  const themes = [["light", "Light"], ["dark", "Dark"], ["system", "Same as system"]];
  const seg = el("div", { class: "segmented", role: "radiogroup", "aria-label": "Theme" },
    themes.map(([v, label]) => {
      const r = el("input", { type: "radio", name: "theme", value: v });
      r.checked = getTheme() === v;
      r.addEventListener("change", () => setTheme(v));
      return el("label", {}, r, label);
    }));

  const r = meta.data_range || {};
  const dataRows = [
    ["Table", meta.table || ""],
    ["Records", r.error ? `Database not reachable: ${r.error}` : `${fmtDay(r.first)} to ${fmtDay(r.last)} (last full day ${fmtDay(r.last_full_day)})`],
    ["Rows per result", nf.format(meta.max_rows || 0)],
    ["Access", "Read-only. Every query needs a time filter and is limited in run time and memory."],
  ];

  view.replaceChildren(el("div", { class: "page-inner" },
    el("div", { class: "page-head" }, el("div", {},
      el("h1", { text: "Settings" }),
      el("p", { text: "Choose the AI that writes the queries. Bring your own key from any supported provider." }))),

    el("section", { class: "panel" },
      el("div", { class: "panel-head" }, el("div", { class: "grow" },
        el("h2", { text: "AI provider" }),
        el("p", { text: "Claude works through Anthropic's API. Every other provider works through its OpenAI-compatible API, including local models with Ollama." }))),
      el("div", { class: "form-grid" },
        el("label", { class: "field" }, el("span", { text: "Provider" }), provider),
        el("div", { class: "field" }, el("span", { class: "field-label", text: "Model" }),
          el("div", { class: "input-row" }, model, loadModels), modelList,
          el("small", { text: "Use Load models to see the exact names this key can use." })),
        baseField,
        el("div", { class: "field full" }, el("span", { class: "field-label", text: "API key" }),
          el("div", { class: "input-row" }, key, showKey), keyHelp,
          el("label", { class: "check" }, remember, "Remember on this device (otherwise kept until the tab closes)")),
        effortField),
      el("div", { class: "form-actions" }, saveBtn, testBtn, forgetBtn, status),
      el("p", { class: "callout", style: "margin-top:14px",
        text: "What the provider receives: your question, the data dictionary, earlier questions in the chat, and sample rows from test queries (up to 15) and results (up to 300 for conclusions). Use a provider your organisation has approved for this data." })),

    voicePanel,

    el("section", { class: "panel" },
      el("div", { class: "panel-head" }, el("div", { class: "grow" },
        el("h2", { text: "Appearance" }),
        el("p", { text: "Day or night theme. Charts redraw in the matching colours." }))),
      seg),

    el("section", { class: "panel" },
      el("div", { class: "panel-head" }, el("div", { class: "grow" }, el("h2", { text: "Data" }))),
      el("table", { class: "list" }, el("tbody", {}, dataRows.map(([k, v]) => el("tr", {}, el("td", { style: "width:180px;color:var(--ink-3)", text: k }), el("td", { text: v }))))))));
}

export function toastSaved() { toast("Settings saved"); }


const ENGINES = [
  ["off", "Off"],
  ["browser", "Browser speech recognition (Chrome, Edge) · free"],
  ["openai", "Whisper-class via OpenAI · most accurate"],
  ["groq", "Whisper via Groq · fast"],
  ["custom", "Other OpenAI-compatible speech endpoint"],
];
const DEFAULT_MODELS = { openai: "gpt-4o-transcribe", groq: "whisper-large-v3", custom: "whisper-1" };

function voiceSettings() {
  const v = getVoice();
  const engine = el("select", { id: "v-engine" }, ENGINES.map(([k, label]) => el("option", {
    value: k, text: k === "browser" && !BrowserSR ? `${label} (not available in this browser)` : label,
    disabled: k === "browser" && !BrowserSR,
  })));
  engine.value = v.engine;
  const language = el("select", { id: "v-lang" }, LANGUAGES.map(([v2, label]) => el("option", { value: v2, text: label })));
  language.value = v.language;
  const model = el("input", { id: "v-model", spellcheck: "false" });
  model.value = v.model;
  const baseUrl = el("input", { id: "v-base", type: "url", spellcheck: "false", placeholder: "https://…/v1" });
  baseUrl.value = v.base_url;
  const key = el("input", { id: "v-key", type: "password", autocomplete: "off", spellcheck: "false" });
  const keyHelp = el("small");
  const autoSend = el("input", { type: "checkbox" });
  autoSend.checked = v.autoSend;
  const engineHelp = el("small");
  const status = el("p", { class: "status-line", role: "status" });
  const tryOut = el("textarea", { class: "input", rows: 2, readonly: true, placeholder: "Your words appear here", style: "height:auto;padding:8px 10px" });
  const tryHint = el("p", { class: "help", text: "Click the mic, say a question, and pause." });
  const tryBtn = el("button", { type: "button", class: "mic-btn mic-lg" });

  const modelField = el("label", { class: "field" }, el("span", { text: "Speech model" }), model,
    el("small", { text: "Leave as is unless your provider names a different model." }));
  const baseField = el("label", { class: "field full" }, el("span", { text: "Speech endpoint base URL" }), baseUrl);
  const keyField = el("label", { class: "field full" }, el("span", { text: "API key for speech" }), key, keyHelp);
  const langField = el("label", { class: "field" }, el("span", { text: "Language you will speak" }), language);

  const sync = () => {
    const e = engine.value;
    const whisper = ["openai", "groq", "custom"].includes(e);
    modelField.hidden = !whisper;
    baseField.hidden = e !== "custom";
    keyField.hidden = !whisper;
    langField.hidden = e === "off";
    model.placeholder = DEFAULT_MODELS[e] || "";
    key.value = whisper ? getKey(voiceKeyName(e)) : "";
    key.placeholder = e === "openai" ? "sk-…" : e === "groq" ? "gsk_…" : "If the endpoint needs one";
    keyHelp.textContent = e === "openai" || e === "groq"
      ? `Stored only in this browser. The same key is used if you pick ${e === "openai" ? "OpenAI" : "Groq"} as the AI provider above.`
      : "Stored only in this browser.";
    engineHelp.textContent = {
      off: "No microphone button in the question box.",
      browser: "Words appear as you speak. Good for plain English; app and place names are less reliable. Chrome sends the audio to Google, Edge to Microsoft.",
      openai: "Most accurate, including app and place names: the audio is transcribed with a hint listing the names in your data. Costs a fraction of a cent per question.",
      groq: "Whisper large v3, very fast, also primed with the names in your data.",
      custom: "Any service with an OpenAI-style /audio/transcriptions endpoint, including a Whisper server on your own network.",
    }[e];
    if (language.value === "" && e === "browser") language.value = "en-IN";
    language.querySelector('option[value=""]').disabled = e === "browser";
  };
  engine.addEventListener("change", sync);
  sync();

  const save = (quiet) => {
    const cfg = { engine: engine.value, language: language.value, model: model.value.trim(), base_url: baseUrl.value.trim(), autoSend: autoSend.checked };
    saveVoice(cfg);
    if (["openai", "groq", "custom"].includes(cfg.engine)) setKey(voiceKeyName(cfg.engine), key.value.trim());
    if (!quiet) { status.textContent = "Saved."; status.className = "status-line ok"; onSaved(); }
    else onSaved(true);
    tryBtn.hidden = cfg.engine === "off";
  };
  const saveBtn = el("button", { type: "button", class: "btn btn-primary", text: "Save", onclick: () => save(false) });

  const checkList = el("ul", { class: "mic-checks", hidden: true });
  const checkBtn = el("button", { type: "button", class: "btn" });
  checkBtn.innerHTML = `${icon("mic", "sm")}<span>Check microphone</span>`;
  checkBtn.addEventListener("click", async () => {
    save(true);
    checkBtn.disabled = true;
    checkList.hidden = false;
    const meter = el("span", { class: "voice-meter" }, [0, 1, 2, 3, 4].map(() => el("i")));
    const listening = el("li", { class: "mic-check live" }, meter, "Say something for four seconds…");
    checkList.replaceChildren();
    const add = (label, ok, detail = "") => {
      const mark = ok === true ? "✓" : ok === false ? "✗" : "•";
      const li = el("li", { class: `mic-check ${ok === true ? "ok" : ok === false ? "bad" : ""}` },
        el("span", { class: "mark", text: mark }), el("span", {}, el("b", { text: label }), detail ? ` · ${detail}` : ""));
      if (listening.isConnected) checkList.insertBefore(li, listening); else checkList.append(li);
    };
    const onLevel = (rms) => {
      if (!listening.isConnected) checkList.append(listening);
      const level = Math.min(1, rms / 0.1);
      [...meter.children].forEach((b, i) => { b.style.height = `${3 + Math.round(13 * Math.max(0, level - i * 0.1))}px`; });
    };
    try { await checkMicrophone(add, onLevel); } finally { listening.remove(); checkBtn.disabled = false; }
  });

  tryBtn.addEventListener("click", () => save(true));
  new VoiceInput({ button: tryBtn, input: tryOut, status: tryHint, onChange: () => {} });

  return el("section", { class: "panel" },
    el("div", { class: "panel-head" }, el("div", { class: "grow" },
      el("h2", { text: "Voice input" }),
      el("p", { text: "Speak a question instead of typing it. The text goes into the box so you can check it before it is sent." }))),
    el("div", { class: "form-grid" },
      el("label", { class: "field" }, el("span", { text: "Speech recognition" }), engine, engineHelp),
      langField, modelField, baseField, keyField,
      el("label", { class: "check full" }, autoSend, "Send the question as soon as I stop speaking (otherwise check it and press Enter)")),
    el("div", { class: "voice-try" }, tryBtn, el("div", { class: "grow" }, tryOut, tryHint)),
    el("div", { class: "form-actions" }, saveBtn, checkBtn, status),
    checkList);
}
