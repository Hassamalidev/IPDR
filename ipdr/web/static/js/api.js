
export const state = { meta: null };


const CFG_KEY = "ipdr.llm.v1";
const keyName = (provider) => `ipdr.llm.key.${provider}`;

function readStore(storage, key) {
  try { return storage.getItem(key); } catch { return null; }
}
function writeStore(storage, key, value) {
  try { value === null ? storage.removeItem(key) : storage.setItem(key, value); } catch {  }
}

export function getLLM() {
  let saved = {};
  try { saved = JSON.parse(readStore(localStorage, CFG_KEY) || "{}"); } catch { saved = {}; }
  const d = (state.meta && state.meta.defaults) || {};
  const provider = saved.provider || d.provider || "anthropic";
  const presets = (state.meta && state.meta.providers) || {};
  const p = presets[provider] || {};
  return {
    provider,
    model: saved.model ?? (provider === d.provider ? d.model : p.default_model) ?? "",
    base_url: saved.base_url ?? "",
    effort: saved.effort || "medium",
    remember: saved.remember !== false,
  };
}

export function getKey(provider) {
  return readStore(sessionStorage, keyName(provider)) || readStore(localStorage, keyName(provider)) || "";
}

export function saveLLM(cfg, key) {
  const { provider, model, base_url, effort, remember } = cfg;
  writeStore(localStorage, CFG_KEY, JSON.stringify({ provider, model, base_url, effort, remember }));
  if (key !== undefined) {
    writeStore(localStorage, keyName(provider), null);
    writeStore(sessionStorage, keyName(provider), null);
    if (key) writeStore(remember ? localStorage : sessionStorage, keyName(provider), key);
  }
}

export function setKey(name, key, remember) {
  if (remember === undefined) remember = !readStore(sessionStorage, keyName(name));
  writeStore(localStorage, keyName(name), null);
  writeStore(sessionStorage, keyName(name), null);
  if (key) writeStore(remember ? localStorage : sessionStorage, keyName(name), key);
}

export function forgetKey(provider) {
  writeStore(localStorage, keyName(provider), null);
  writeStore(sessionStorage, keyName(provider), null);
}

export function aiReady() {
  const cfg = getLLM();
  const presets = (state.meta && state.meta.providers) || {};
  const p = presets[cfg.provider];
  if (!p || !cfg.model) return false;
  if (!p.needs_key) return true;
  if (getKey(cfg.provider)) return true;
  const d = (state.meta && state.meta.defaults) || {};
  return d.server_has_key && d.provider === cfg.provider;
}

function llmBody(cfg = getLLM()) {
  return { provider: cfg.provider, model: cfg.model, base_url: cfg.base_url, effort: cfg.effort };
}

function headers(json = true, cfg = getLLM(), keyOverride) {
  const h = {};
  if (json) h["Content-Type"] = "application/json";
  const key = keyOverride !== undefined ? keyOverride : getKey(cfg.provider);
  if (key) h["X-LLM-Key"] = key;
  return h;
}


export class ApiError extends Error {}

async function parse(resp) {
  let data = null;
  try { data = await resp.json(); } catch { data = null; }
  if (!resp.ok) {
    let msg = (data && (data.detail || data.message || data.error)) || `Request failed (${resp.status}).`;
    if (Array.isArray(msg)) {
      msg = msg.map((d) => String((d && d.msg) || "").replace(/^Value error, /, "")).filter(Boolean).join(" ") || "The request was not valid.";
    }
    throw new ApiError(typeof msg === "string" ? msg : "Request failed.");
  }
  return data;
}

export async function get(url) {
  return parse(await fetch(url));
}
export async function send(method, url, body) {
  return parse(await fetch(url, { method, headers: headers(true), body: body === undefined ? undefined : JSON.stringify(body) }));
}

export const api = {
  meta: () => get("/api/meta"),
  conversations: (q = "") => get(`/api/conversations?q=${encodeURIComponent(q)}`),
  conversation: (id) => get(`/api/conversations/${id}`),
  rename: (id, title) => send("PATCH", `/api/conversations/${id}`, { title }),
  remove: (id) => send("DELETE", `/api/conversations/${id}`),
  rewind: (id, messageId) => send("POST", `/api/conversations/${id}/rewind`, { message_id: messageId }),
  runSql: (conversation_id, sql) => send("POST", "/api/sql", { conversation_id, sql }),
  conclude: (mid) => send("POST", `/api/messages/${mid}/conclude`, { llm: llmBody() }),
  verify: (mid) => send("POST", `/api/messages/${mid}/verify`, {}),
  chooseChart: (mid, chart) => send("PATCH", `/api/messages/${mid}`, { chart }),
  setView: (mid, view) => send("PATCH", `/api/messages/${mid}`, view),
  dictionary: () => get("/api/dictionary"),
  dictionaryPrompt: async () => (await fetch("/api/dictionary/prompt")).text(),
  toggleDictionary: (id, active) => send("PATCH", `/api/dictionary/${id}`, { active }),
  deleteDictionary: (id) => send("DELETE", `/api/dictionary/${id}`),
  async uploadDictionary(file, name) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", name || "");
    return parse(await fetch("/api/dictionary", { method: "POST", body: fd }));
  },
  examples: () => get("/api/examples"),
  addExample: (question, sql, notes) => send("POST", "/api/examples", { question, sql, notes }),
  deleteExample: (id) => send("DELETE", `/api/examples/${id}`),
  async testLLM(cfg, key) {
    return parse(await fetch("/api/llm/test", { method: "POST", headers: headers(true, cfg, key), body: JSON.stringify({ llm: llmBody(cfg) }) }));
  },
  async listModels(cfg, key) {
    return parse(await fetch("/api/llm/models", { method: "POST", headers: headers(true, cfg, key), body: JSON.stringify({ llm: llmBody(cfg) }) }));
  },
};

export async function streamChat({ conversationId, question, signal, onEvent }) {
  const resp = await fetch("/api/chat", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify({ conversation_id: conversationId || null, question, llm: llmBody() }),
    signal,
  });
  if (!resp.ok) await parse(resp);
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i);
      buf = buf.slice(i + 1);
      if (line.trim()) onEvent(JSON.parse(line));
    }
  }
}
