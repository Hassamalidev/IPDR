
import { getKey } from "./api.js";
import { icon } from "./icons.js";
import { el } from "./util.js";

const KEY = "ipdr.voice.v1";
export const BrowserSR = window.SpeechRecognition || window.webkitSpeechRecognition || null;
export const voiceKeyName = (engine) => (engine === "custom" ? "voice-custom" : engine);

export const LANGUAGES = [
  ["en-IN", "English (Pakistan / India accent)"],
  ["en-US", "English (US accent)"],
  ["en-GB", "English (UK accent)"],
  ["ur-PK", "Urdu"],
  ["", "Detect automatically (Whisper only)"],
];

const SILENCE_AFTER_SPEECH_MS = 3000;
const WAIT_FOR_SPEECH_MS = 10000;
const MAX_MS = 90000;

export function getVoice() {
  let v = {};
  try { v = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { v = {}; }
  let language = v.language ?? "en-IN";
  if (language === "en") language = "en-IN";
  if (language === "ur") language = "ur-PK";
  return {
    engine: v.engine || (BrowserSR ? "browser" : "off"),
    language,
    model: v.model || "",
    base_url: v.base_url || "",
    autoSend: !!v.autoSend,
  };
}

export function saveVoice(v) {
  try { localStorage.setItem(KEY, JSON.stringify(v)); } catch {  }
}

const whisperLang = (lang) => (lang ? lang.slice(0, 2) : "");

export function micError(e) {
  const name = e && (e.name || e.error);
  if (!window.isSecureContext) return "The microphone only works on https:// or on this computer (http://localhost or http://127.0.0.1). Open the app that way.";
  if (name === "NotAllowedError" || name === "not-allowed" || name === "service-not-allowed" || name === "SecurityError")
    return "Microphone access is blocked. Click the icon left of the address bar, allow the microphone, then try again.";
  if (name === "NotFoundError" || name === "audio-capture" || name === "OverconstrainedError") return "No microphone was found. Plug one in, or pick it as the input device in Windows sound settings.";
  if (name === "NotReadableError" || name === "AbortError") return "The microphone is busy in another app (Teams, Zoom, WhatsApp…). Close it and try again.";
  if (name === "PromptTimeout") return "The browser did not get permission to use the microphone. Click Allow when it asks, then try again.";
  if (name === "network") return "The browser's speech service could not be reached (Chrome uses Google, Edge uses Microsoft, both need internet). Try again, or choose Whisper under Settings → Voice input.";
  if (name === "language-not-supported") return "This browser cannot recognise the chosen language. Pick another under Settings → Voice input.";
  return `The microphone could not start (${name || "unknown error"}).`;
}

function pickMime() {
  const types = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
  return types.find((t) => window.MediaRecorder && MediaRecorder.isTypeSupported(t)) || "";
}

const join = (...parts) => parts.map((p) => (p || "").trim()).filter(Boolean).join(" ");

function levelMeter(stream, onLevel) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  const ctx = new Ctx();
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  ctx.createMediaStreamSource(stream).connect(analyser);
  const buf = new Float32Array(analyser.fftSize);
  if (ctx.state === "suspended") ctx.resume().catch(() => {});
  const id = setInterval(() => {
    analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (const v of buf) sum += v * v;
    onLevel(Math.sqrt(sum / buf.length));
  }, 100);
  return () => { clearInterval(id); ctx.close().catch(() => {}); };
}

async function openMic() {
  if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    throw Object.assign(new Error("insecure"), { name: "insecure" });
  }
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(Object.assign(new Error("timeout"), { name: "PromptTimeout" })), 15000);
  });
  try {
    return await Promise.race([
      navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      }),
      timeout,
    ]);
  } finally {
    clearTimeout(timer);
  }
}

const instances = new Set();

export function cancelAllVoice() {
  instances.forEach((v) => {
    if (!v.button.isConnected) { v.cancel(); instances.delete(v); } else v.cancel();
  });
}

export class VoiceInput {
  constructor({ button, input, status, onChange = () => {}, onDone = () => {} }) {
    Object.assign(this, { button, input, status, onChange, onDone });
    this.state = "idle";
    this.statusText = status ? status.textContent : "";
    this.baseClass = status ? status.className : "";
    this.meter = el("span", { class: "voice-meter", "aria-hidden": "true" }, [0, 1, 2, 3, 4].map(() => el("i")));
    button.addEventListener("click", () => this.toggle());
    instances.forEach((v) => { if (!v.button.isConnected) { v.cancel(); instances.delete(v); } });
    instances.add(this);
    this.render();
  }

  cancel() {
    if (this.state === "idle") return;
    this.cancelled = true;
    this.stopping = true;
    clearTimeout(this.watchdog);
    if (this.rec) {
      const rec = this.rec;
      rec.onresult = rec.onerror = rec.onend = null;
      try { rec.abort(); } catch {  }
    }
    if (this.recorder && this.recorder.state !== "inactive") {
      this.recorder.onstop = null;
      try { this.recorder.stop(); } catch {  }
    }
    this.cleanup();
    if (this.base !== undefined) { this.input.value = this.base; this.onChange(); }
    this.state = "idle";
    this.render();
    this.restore();
  }

  get config() { return getVoice(); }

  render() {
    const b = this.button;
    b.hidden = this.config.engine === "off";
    b.classList.toggle("listening", this.state === "listening");
    b.classList.toggle("busy", this.state === "transcribing");
    b.disabled = this.state === "transcribing";
    b.innerHTML = icon(this.state === "listening" ? "stop" : "mic");
    const label = this.state === "listening" ? "Stop listening" : this.state === "transcribing" ? "Turning speech into text" : "Speak your question";
    b.setAttribute("aria-label", label);
    b.title = label;
    b.setAttribute("aria-pressed", String(this.state === "listening"));
  }

  say(text, kind = "", withMeter = false) {
    if (!this.status) return;
    this.status.replaceChildren();
    this.status.className = `${this.baseClass} ${kind}`.trim();
    if (withMeter) this.status.append(this.meter, " ");
    this.status.append(text);
  }

  restore(delay = 0) {
    clearTimeout(this._restoreTimer);
    this._restoreTimer = setTimeout(() => {
      if (this.state !== "idle" || !this.status) return;
      this.status.className = this.baseClass;
      this.status.textContent = this.statusText;
    }, delay);
  }

  showLevel(rms) {
    const level = Math.min(1, rms / 0.1);
    [...this.meter.children].forEach((b, i) => { b.style.height = `${3 + Math.round(13 * Math.max(0, level - i * 0.1))}px`; });
  }

  fail(message) {
    this.cleanup();
    this.state = "idle";
    this.render();
    this.say(message, "bad");
    this.restore(9000);
  }

  toggle() {
    if (this.state === "listening") this.stop();
    else if (this.state === "idle") this.start();
  }

  start() {
    const cfg = this.config;
    if (cfg.engine === "off") return;
    this.stopping = false;
    this.cancelled = false;
    this.base = this.input.value;
    this.input.readOnly = true;
    if (cfg.engine === "browser") {
      if (!BrowserSR) return this.fail("This browser has no speech recognition. Use Chrome or Edge, or choose Whisper under Settings → Voice input.");
      return this.startBrowser(cfg);
    }
    if (!getKey(voiceKeyName(cfg.engine)) && cfg.engine !== "custom") {
      return this.fail("Voice input needs an API key. Add it under Settings → Voice input.");
    }
    return this.startRecorder(cfg);
  }

  stop() {
    if (this.stopping && this.watchdog) return;
    this.stopping = true;
    if (this.rec) { try { this.rec.stop(); } catch {  } }
    if (this.recorder && this.recorder.state !== "inactive") this.recorder.stop();
    clearTimeout(this.watchdog);
    this.watchdog = setTimeout(() => {
      if (this.state !== "listening") return;
      if (this.rec) {
        const rec = this.rec;
        const onend = rec.onend;
        rec.onresult = rec.onerror = rec.onend = null;
        try { rec.abort(); } catch {  }
        if (onend) onend();
      } else if (this.forceFinish) {
        this.forceFinish();
      }
    }, 1500);
  }

  cleanup() {
    clearInterval(this.tick);
    clearTimeout(this.watchdog);
    this.watchdog = null;
    if (this.stopMeter) this.stopMeter();
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    this.stopMeter = this.stream = this.recorder = this.rec = this.forceFinish = null;
    this.input.readOnly = false;
  }

  insert(base, text) {
    const sep = base && !/\s$/.test(base) && text ? " " : "";
    this.input.value = base + sep + text;
    this.onChange();
  }

  finishWith(base, text) {
    this.state = "idle";
    this.render();
    if (!text) {
      this.insert(base, "");
      this.say("Nothing was understood. Speak clearly, close to the microphone, and try again.", "bad");
      this.restore(7000);
      return;
    }
    this.insert(base, text);
    this.say("Check the text, then press Enter to ask.", "ok");
    this.restore(6000);
    this.input.focus();
    this.onDone(this.input.value);
  }


  async startBrowser(cfg) {
    const base = this.input.value;
    const started = performance.now();
    let committed = "";
    let sessionFinal = "";
    let sessionInterim = "";
    let heard = false;
    let lastWord = started;
    let failed = false;
    let networkRetries = 0;
    let peak = 0;
    let silentWarned = false;

    this.state = "listening";
    this.render();
    this.say("Starting the microphone…", "live", true);

    try {
      this.stream = await openMic();
      this.stopMeter = levelMeter(this.stream, (rms) => { peak = Math.max(peak, rms); this.showLevel(rms); });
    } catch (e) {
      if (e.name === "NotAllowedError" || e.name === "SecurityError" || e.name === "insecure" || e.name === "NotFoundError") return this.fail(micError(e));
    }
    if (this.stopping) { this.cleanup(); this.state = "idle"; this.render(); this.restore(); return; }

    const text = () => join(committed, sessionFinal, sessionInterim);

    const startSession = () => {
      const rec = new BrowserSR();
      this.rec = rec;
      rec.lang = cfg.language || "en-IN";
      rec.continuous = true;
      rec.interimResults = true;
      rec.maxAlternatives = 1;

      rec.onresult = (ev) => {
        let fin = "";
        let interim = "";
        for (let i = 0; i < ev.results.length; i++) {
          const t = ev.results[i][0].transcript;
          if (ev.results[i].isFinal) fin += ` ${t}`;
          else interim += ` ${t}`;
        }
        sessionFinal = fin.trim();
        sessionInterim = interim.trim();
        if (sessionFinal || sessionInterim) { heard = true; lastWord = performance.now(); }
        this.insert(base, text());
      };
      rec.onerror = (ev) => {
        if (ev.error === "no-speech" || ev.error === "aborted") return;
        if (ev.error === "network" && networkRetries < 2) { networkRetries += 1; return; }
        failed = true;
        this.insert(base, text());
        this.fail(micError(ev));
      };
      rec.onend = () => {
        committed = join(committed, sessionFinal, sessionInterim);
        sessionFinal = sessionInterim = "";
        if (failed) return;
        const elapsed = performance.now() - started;
        if (!this.stopping && elapsed < MAX_MS) {
          try { startSession(); return; } catch {  }
        }
        finish();
      };
      rec.start();
    };

    const finish = async () => {
      if (this.cancelled) return;
      clearInterval(this.tick);
      const raw = committed.trim();
      this.cleanup();
      if (!raw) {
        this.state = "idle";
        this.render();
        this.insert(base, "");
        this.say(peak < 0.004
          ? "The microphone picked up no sound. Check that the right microphone is selected and not muted, then try again."
          : "No words were recognised. Speak a little louder and closer, or try Whisper under Settings → Voice input.", "bad");
        this.restore(9000);
        return;
      }
      const cleaned = await cleanText(raw);
      if (!this.cancelled) this.finishWith(base, cleaned);
    };

    this.tick = setInterval(() => {
      const now = performance.now();
      const secs = Math.round((now - started) / 1000);
      if (!silentWarned && now - started > 3500 && peak < 0.004 && this.stopMeter) {
        silentWarned = true;
        this.say("The microphone hears nothing. Check it is not muted and is the selected input device.", "bad", true);
      } else if (!silentWarned || peak >= 0.004) {
        this.say(heard ? `Listening… ${secs}s · pause for 3 seconds or click the mic to finish` : "Listening… speak now", "live", true);
      }
      if (heard && now - lastWord > SILENCE_AFTER_SPEECH_MS) this.stop();
      else if (!heard && now - started > WAIT_FOR_SPEECH_MS) this.stop();
      else if (now - started > MAX_MS) this.stop();
    }, 200);

    try { startSession(); } catch (e) { this.fail(micError(e)); }
  }


  async startRecorder(cfg) {
    if (!window.MediaRecorder) return this.fail("This browser cannot record audio. Use a current Chrome, Edge or Firefox.");
    this.state = "listening";
    this.render();
    this.say("Starting the microphone…", "live", true);
    try {
      this.stream = await openMic();
    } catch (e) {
      return this.fail(micError(e));
    }
    if (this.stopping) { this.cleanup(); this.state = "idle"; this.render(); this.restore(); return; }
    const mime = pickMime();
    const recorder = new MediaRecorder(this.stream, mime ? { mimeType: mime, audioBitsPerSecond: 64000 } : undefined);
    this.recorder = recorder;
    const chunks = [];
    recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };

    const started = performance.now();
    let noise = 0.003, peak = 0, loudFrames = 0, spoke = false, lastVoice = started;
    this.stopMeter = levelMeter(this.stream, (rms) => {
      this.showLevel(rms);
      peak = Math.max(peak, rms);
      noise = rms < noise ? noise * 0.7 + rms * 0.3 : noise * 0.995 + rms * 0.005;
      const threshold = Math.max(0.006, noise * 3);
      loudFrames = rms > threshold ? loudFrames + 1 : 0;
      const now = performance.now();
      if (loudFrames >= 2) { spoke = true; lastVoice = now; }
      const secs = Math.round((now - started) / 1000);
      if (spoke && now - lastVoice > SILENCE_AFTER_SPEECH_MS - 800 && now - started > 1500) this.stop();
      else if (!spoke && now - started > WAIT_FOR_SPEECH_MS) this.stop();
      else if (now - started > MAX_MS) this.stop();
      if (this.state === "listening" && !this.stopping) {
        if (now - started > 3500 && peak < 0.004) this.say("The microphone hears nothing. Check it is not muted and is the selected input device.", "bad", true);
        else this.say(spoke ? `Listening… ${secs}s · pause or click the mic to finish` : "Listening… speak now", "live", true);
      }
    });

    this.forceFinish = () => { const f = recorder.onstop; recorder.onstop = null; if (f) f(); };
    recorder.onstop = async () => {
      if (this.cancelled) return;
      const silent = peak < 0.004;
      this.cleanup();
      if (silent) {
        this.state = "idle";
        this.render();
        this.say("The microphone picked up no sound. Check that the right microphone is selected and not muted, then try again.", "bad");
        this.restore(9000);
        return;
      }
      this.state = "transcribing";
      this.render();
      this.say("Turning speech into text…", "live");
      const blob = new Blob(chunks, { type: recorder.mimeType || mime || "audio/webm" });
      const ext = (blob.type.split(";")[0].split("/")[1] || "webm").replace("mpeg", "mp3");
      const base = this.input.value;
      try {
        const text = await transcribe(blob, `speech.${ext}`, cfg);
        if (this.cancelled) return;
        this.finishWith(base, text);
      } catch (e) {
        if (!this.cancelled) this.fail(e.message);
      }
    };
    recorder.start(250);
    this.say("Listening… speak now", "live", true);
  }
}

async function cleanText(text) {
  try {
    const r = await fetch("/api/voice/clean", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
    if (r.ok) return (await r.json()).text;
  } catch {  }
  return text.trim();
}

export async function transcribe(blob, filename, cfg) {
  const fd = new FormData();
  fd.append("audio", blob, filename);
  fd.append("provider", cfg.engine);
  fd.append("model", cfg.model || "");
  fd.append("language", whisperLang(cfg.language));
  fd.append("base_url", cfg.base_url || "");
  const headers = {};
  const key = getKey(voiceKeyName(cfg.engine));
  if (key) headers["X-LLM-Key"] = key;
  let resp;
  try {
    resp = await fetch("/api/voice/transcribe", { method: "POST", headers, body: fd });
  } catch {
    throw new Error("Cannot reach the server.");
  }
  let data = {};
  try { data = await resp.json(); } catch { data = {}; }
  if (!resp.ok) throw new Error(data.detail || "The speech could not be turned into text.");
  return data.text;
}


export async function checkMicrophone(report, onLevel = () => {}) {
  const cfg = getVoice();
  report("Page opened securely (https or localhost)", window.isSecureContext,
    window.isSecureContext ? "" : `This address (${location.host}) blocks the microphone. Use http://localhost:8000 or https.`);
  report("Browser can use a microphone", !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia));
  if (cfg.engine === "browser") {
    report("Browser speech recognition available", !!BrowserSR, BrowserSR ? "" : "Use Chrome or Edge, or choose Whisper.");
  } else if (cfg.engine !== "off") {
    report("Browser can record audio", !!window.MediaRecorder);
    report("Speech API key saved", cfg.engine === "custom" || !!getKey(voiceKeyName(cfg.engine)));
  }
  try {
    const p = await navigator.permissions.query({ name: "microphone" });
    report("Microphone permission", p.state !== "denied",
      p.state === "granted" ? "Allowed" : p.state === "prompt" ? "The browser will ask: click Allow" : "Blocked: allow it from the icon left of the address bar");
  } catch {  }
  let stream;
  try {
    stream = await openMic();
  } catch (e) {
    report("Microphone opens", false, micError(e));
    return;
  }
  const track = stream.getAudioTracks()[0];
  report("Microphone in use", null, track ? track.label || "Default microphone" : "Unknown");
  let peak = 0;
  const stopMeter = levelMeter(stream, (rms) => { peak = Math.max(peak, rms); onLevel(rms); });
  await new Promise((r) => setTimeout(r, 4000));
  stopMeter();
  stream.getTracks().forEach((t) => t.stop());
  if (peak < 0.004) report("Sound level while you spoke", false, "No sound at all. The microphone may be muted, or a different one may be selected in Windows sound settings.");
  else if (peak < 0.03) report("Sound level while you spoke", true, "Quiet but usable. Speak a little closer for best results.");
  else report("Sound level while you spoke", true, "Good.");
}
