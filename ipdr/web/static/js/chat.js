
import { aiReady, api, getLLM, state, streamChat } from "./api.js";
import { renderAssistant, renderPending, renderUser } from "./answer.js";
import { icon } from "./icons.js";
import { $, debounce, el, recencyGroup, toast } from "./util.js";
import { releaseDetached } from "./answer.js";
import { cancelAllVoice, getVoice, VoiceInput } from "./voice.js";

let voice = null;
export function refreshVoice() { if (voice) voice.render(); }

const chat = {
  id: null,
  view: 0,
  running: null,
  runConv: null,
  pending: null,
  list: [],
};

let thread, inner, input, sendBtn, form;


async function refreshList() {
  try {
    chat.list = await api.conversations($("#conv-search").value.trim());
  } catch {
    chat.list = [];
  }
  renderList();
}

function renderList() {
  const box = $("#conv-list");
  box.replaceChildren();
  if (!chat.list.length) {
    box.append(el("p", { class: "conv-empty", text: $("#conv-search").value ? "No conversations match." : "No conversations yet." }));
    return;
  }
  let group = null;
  let groupEl = null;
  for (const c of chat.list) {
    const g = recencyGroup(c.updated_at);
    if (g !== group) {
      group = g;
      groupEl = el("div", { class: "conv-group" }, el("h4", { text: g }));
      box.append(groupEl);
    }
    groupEl.append(convItem(c));
  }
}

function convItem(c) {
  const item = el("div", { class: `conv-item${c.id === chat.id ? " active" : ""}`, dataset: { id: c.id } });
  const link = el("a", { href: `#/chat/${c.id}`, title: c.title || "Untitled", text: c.title || "Untitled" });
  if (c.id === chat.id) link.setAttribute("aria-current", "page");
  const rename = el("button", { type: "button", class: "icon-btn", title: "Rename", "aria-label": "Rename", html: icon("pencil", "sm") });
  const del = el("button", { type: "button", class: "icon-btn", title: "Delete", "aria-label": "Delete", html: icon("trash", "sm") });
  item.append(link, el("div", { class: "conv-actions" }, rename, del));

  rename.addEventListener("click", () => {
    const field = el("input", { class: "input", value: c.title || "", "aria-label": "Conversation name" });
    item.replaceChildren(field);
    field.focus();
    field.select();
    let done = false;
    const finish = async (save) => {
      if (done) return;
      done = true;
      if (save && field.value.trim() && field.value.trim() !== c.title) {
        try { await api.rename(c.id, field.value.trim()); } catch (e) { toast(e.message); }
      }
      refreshList();
    };
    field.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); });
    field.addEventListener("blur", () => finish(true));
  });

  del.addEventListener("click", () => {
    const yes = el("button", { type: "button", class: "btn btn-sm btn-danger", text: "Delete" });
    const no = el("button", { type: "button", class: "btn btn-sm btn-ghost", text: "Keep" });
    item.replaceChildren(el("div", { class: "conv-confirm" }, el("span", { text: "Delete this chat?" }), no, yes));
    no.addEventListener("click", renderList);
    yes.addEventListener("click", async () => {
      try {
        await api.remove(c.id);
        if (chat.id === c.id) location.hash = "#/chat";
      } catch (e) { toast(e.message); }
      refreshList();
    });
    yes.focus();
  });
  return item;
}


function scrollToEnd() {
  requestAnimationFrame(() => { thread.scrollTop = thread.scrollHeight; });
}

function append(msg) {
  const node = msg.role === "user" ? renderUser(msg, { onEdit: editQuestion }) : renderAssistant(msg);
  inner.append(node);
  if (msg.role === "user" && msg.unanswered) inner.append(unansweredNote(msg));
  return node;
}

function runActions(msg) {
  const d = msg.data || {};
  if (!d.actions || !d.actions.length || !d.target) return;
  const card = inner.querySelector(`.answer[data-id="${CSS.escape(d.target)}"]`);
  if (!card || !card._act) return;
  const done = d.actions.map((a) => card._act(a)).filter(Boolean);
  if (done.length) toast(done.join(" "));
  if (d.actions.some((a) => a === "chart" || a === "conclusion")) {
    setTimeout(() => card.querySelector(d.actions.includes("chart") ? ".chart-section" : ".conclusion")?.scrollIntoView({ block: "center", behavior: "smooth" }), 150);
  }
}

function unansweredNote(msg) {
  if (msg.unanswered === "working") {
    return el("div", { class: "reply notice" },
      el("p", { class: "reply-text", text: "Still working on this question. Open this conversation again in a moment to see the answer." }));
  }
  const note = el("div", { class: "reply notice" },
    el("p", { class: "reply-text", text: "This question was not answered: the app restarted or the connection dropped before it finished." }));
  const again = el("button", { type: "button", class: "btn btn-sm", text: "Ask again", onclick: async () => {
    if (chat.running) { toast("Wait for the current answer to finish."); return; }
    if (!canAsk(msg.content)) return;
    note.remove();
    await ask(msg.content);
  } });
  note.append(el("div", { class: "notice-actions" }, again));
  return note;
}

function welcome() {
  const box = el("div", { class: "welcome" },
    el("h1", { text: "What do you want to know?" }),
    el("p", { text: "Ask in your own words about app usage, places, times, servers or subscribers. You get the answer as a table, and can then draw a chart or get a written conclusion." }));
  if (!aiReady()) {
    box.append(el("div", { class: "welcome-notice" },
      el("strong", { text: "Connect an AI provider to start. " }),
      "Add your API key in ", el("a", { href: "#/settings", text: "Settings" }),
      ". To look at records without the AI, use ", el("a", { href: "#/explore", text: "Explore" }), "."));
  }
  const sug = el("div", { class: "suggestions" });
  box.append(el("p", { class: "suggestions-title", text: "Try one of these" }), sug);
  api.examples().then((list) => {
    list.filter((e) => e.source === "built-in").slice(0, 6).forEach((e) => {
      sug.append(el("button", { type: "button", text: e.question, onclick: () => {
        if (chat.running) { toast("Wait for the current answer to finish."); return; }
        input.value = e.question; autosize(); submit();
      } }));
    });
  }).catch(() => {});
  return box;
}

function rememberConversation(id) {
  const link = document.querySelector('.nav-links a[data-route="chat"]');
  if (link) link.setAttribute("href", id ? `#/chat/${id}` : "#/chat");
}

export async function showChat(id) {
  cancelAllVoice();
  chat.id = id || null;
  chat.view += 1;
  const view = chat.view;
  inner.replaceChildren();
  releaseDetached();
  rememberConversation(chat.id);
  if (!chat.id) {
    inner.append(welcome());
    renderList();
    input.focus();
    return;
  }
  try {
    const conv = await api.conversation(chat.id);
    if (view !== chat.view) return;
    conv.messages.forEach(append);
    if (!conv.messages.length) inner.append(welcome());
    if (chat.running && chat.runConv === chat.id && chat.pending) inner.append(chat.pending.node);
    scrollToEnd();
  } catch (e) {
    if (view !== chat.view) return;
    inner.append(el("div", { class: "reply error" }, el("p", { text: e.message })));
  }
  renderList();
  input.focus();
}


function autosize() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 220)}px`;
  sendBtn.disabled = !chat.running && !input.value.trim();
}

function setRunning(ctrl) {
  chat.running = ctrl;
  sendBtn.classList.toggle("stop", !!ctrl);
  sendBtn.innerHTML = icon(ctrl ? "stop" : "send");
  sendBtn.setAttribute("aria-label", ctrl ? "Stop" : "Send");
  sendBtn.title = ctrl ? "Stop" : "Send";
  sendBtn.disabled = !ctrl && !input.value.trim();
}

function stop() {
  if (chat.running) chat.running.abort();
}

const MAX_QUESTION = 4000;

async function submit() {
  const text = input.value.trim();
  if (!text || chat.running) return;
  if (!canAsk(text)) return;
  input.value = "";
  autosize();
  await ask(text);
}

function canAsk(text) {
  if (text.length < 2) { toast("Type a question first."); return false; }
  if (text.length > MAX_QUESTION) { toast(`Questions can be up to ${MAX_QUESTION} characters. Shorten it a little.`); return false; }
  if (!aiReady()) {
    toast("Add an AI provider and key in Settings first.");
    location.hash = "#/settings";
    return false;
  }
  return true;
}

async function editQuestion(node, text) {
  if (chat.running) throw new Error("Wait for the current answer to finish, or stop it, before editing.");
  if (!chat.id || !node.dataset.id) throw new Error("This question cannot be edited yet.");
  if (!canAsk(text)) throw new Error("");
  await api.rewind(chat.id, node.dataset.id);
  for (let n = node; n; ) { const next = n.nextSibling; n.remove(); n = next; }
  releaseDetached();
  await ask(text);
}

async function ask(text) {
  if (voice && voice.state === "listening") voice.stop();
  const view = chat.view;
  if (!chat.id) inner.replaceChildren();
  const userNode = renderUser({ id: "", kind: "question", content: text }, { onEdit: editQuestion });
  inner.append(userNode);
  const pending = renderPending();
  inner.append(pending.node);
  scrollToEnd();

  const ctrl = new AbortController();
  setRunning(ctrl);
  chat.pending = pending;
  chat.runConv = chat.id;
  let got = false;
  let saved = false;
  const here = () => chat.runConv !== null && chat.id === chat.runConv;
  try {
    await streamChat({
      conversationId: chat.id,
      question: text,
      signal: ctrl.signal,
      onEvent: (ev) => {
        if (ev.type === "conversation") {
          chat.runConv = ev.id;
          if (view === chat.view && !chat.id) {
            chat.id = ev.id;
            history.replaceState(null, "", `#/chat/${ev.id}`);
            rememberConversation(ev.id);
          }
          refreshList();
        } else if (ev.type === "user_message") {
          saved = true;
          userNode.dataset.id = ev.message.id;
        } else if (ev.type === "step") {
          pending.step(ev.text);
          if (here()) scrollToEnd();
        } else if (ev.type === "message") {
          got = true;
          pending.done();
          if (here()) { append(ev.message); scrollToEnd(); runActions(ev.message); }
          else toast("An answer is ready in another conversation.");
          const sr = $("#sr-status");
          if (sr) sr.textContent = ev.message.kind === "result" ? `Answer ready: ${ev.message.content}` : ev.message.content;
        }
      },
    });
    if (!got) throw new Error("The server stopped without an answer. Try again.");
  } catch (e) {
    pending.done();
    if (!saved) {
      userNode.remove();
      if (!input.value.trim()) { input.value = text; autosize(); }
    }
    const stopped = e.name === "AbortError";
    if (here() || (!saved && view === chat.view)) {
      inner.append(renderAssistant({ id: "", role: "assistant", kind: "error", content: stopped ? "Stopped." : e.message, data: { stopped } }));
      scrollToEnd();
    } else if (!stopped) {
      toast(e.message);
    }
  } finally {
    pending.done();
    chat.pending = null;
    chat.runConv = null;
    setRunning(null);
    refreshList();
    if (view === chat.view) input.focus();
  }
}

export function initChat() {
  thread = $("#thread");
  inner = el("div", { class: "thread-inner" });
  thread.append(inner);
  input = $("#input");
  sendBtn = $("#send");
  form = $("#composer");
  sendBtn.innerHTML = icon("send");

  $("#new-chat").innerHTML = `${icon("plus", "sm")}<span>New chat</span>`;
  $("#new-chat").addEventListener("click", () => {
    if (location.hash === "#/chat" || location.hash === "") showChat(null);
    else location.hash = "#/chat";
    document.body.classList.remove("sidebar-open");
  });
  $("#conv-search").addEventListener("input", debounce(refreshList, 200));

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (chat.running) stop(); else submit();
  });
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      submit();
    }
  });
  autosize();
  refreshList();

  voice = new VoiceInput({
    button: $("#mic"),
    input,
    status: $("#composer-hint"),
    onChange: autosize,
    onDone: () => {
      input.focus();
      if (getVoice().autoSend && input.value.trim()) submit();
    },
  });
}

export function currentModelLabel() {
  const cfg = getLLM();
  const p = (state.meta && state.meta.providers && state.meta.providers[cfg.provider]) || {};
  return `${p.label || cfg.provider} · ${cfg.model || "no model"}`;
}
