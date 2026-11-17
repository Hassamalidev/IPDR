from __future__ import annotations

import re
import time

import requests

from . import net

from .config import settings
from .db import QueryError, db

PROVIDERS = {
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-transcribe"},
    "groq": {"label": "Groq", "base_url": "https://api.groq.com/openai/v1", "model": "whisper-large-v3"},
    "custom": {"label": "Custom endpoint", "base_url": "", "model": "whisper-1"},
}
MAX_BYTES = 25_000_000
LANGUAGES = {"", "en", "ur"}

FALLBACK_APPS = ["TikTok", "WhatsApp", "Facebook", "YouTube", "Snapchat", "Instagram", "Roblox", "iCloud",
                 "Google Meet", "IMO", "ChatGPT", "Netflix", "Spotify", "Free Fire", "Gmail", "Telegram"]
FALLBACK_CITIES = ["Karachi", "Lahore", "Islamabad", "Rawalpindi", "Faisalabad", "Multan", "Peshawar", "Quetta",
                   "Gujranwala", "Sialkot", "Hyderabad", "Sargodha", "Bahawalpur"]
AREAS = ["Model Town", "Gulberg", "DHA", "Clifton", "Satellite Town", "Johar Town", "Blue Area", "Saddar"]

FIXES = [
    (r"\btick\s*tock\b|\btik\s*tok\b", "TikTok"),
    (r"\bwhats?\s*app\b|\bwhat'?s\s*up\b(?=\s+(users|usage|traffic|data|calls|messages))", "WhatsApp"),
    (r"\byou\s*tube\b", "YouTube"),
    (r"\bsnap\s*chat\b", "Snapchat"),
    (r"\binsta\s*gram\b", "Instagram"),
    (r"\bi\s*m\s*o\b|\bimo\b", "IMO"),
    (r"\bchat\s*g\s*p\s*t\b", "ChatGPT"),
    (r"\bfree\s*fire\b", "Free Fire"),
    (r"\bgoogle\s*meet\b", "Google Meet"),
    (r"\bi\s*cloud\b", "iCloud"),
    (r"\bi\s*p\s*d\s*r\b", "IPDR"),
    (r"\bd\s*h\s*a\b", "DHA"),
]


class VoiceError(Exception):
    pass


_vocab = {"at": 0.0, "text": ""}


def vocabulary() -> str:
    if _vocab["text"] and time.time() - _vocab["at"] < 3600:
        return _vocab["text"]
    apps, cities = list(FALLBACK_APPS), list(FALLBACK_CITIES)
    try:
        day_sql = f"start_time >= toUnixTimestamp(today() - 2, '{settings.timezone}') AND start_time < toUnixTimestamp(today() - 1, '{settings.timezone}')"
        res = db.cached_query(
            f"SELECT replaceOne(client_location, ' Pakistan', '') AS c FROM {settings.full_table} "
            f"WHERE {day_sql} AND client_location LIKE '% Pakistan' GROUP BY c ORDER BY count() DESC LIMIT 25",
            max_rows=25, ttl=3600, timeout=20)
        found = [r[0] for r in res.rows if r[0] and r[0] not in ("Punjab", "Sindh")]
        if found:
            cities = list(dict.fromkeys(found + cities))[:25]
    except QueryError:
        pass
    text = ("Questions about mobile network usage records (IPDR) in Pakistan. "
            f"Apps: {', '.join(apps)}. Cities: {', '.join(cities)}. Areas: {', '.join(AREAS)}. "
            "Terms: users, subscribers, connections, data volume, GB, last week, yesterday, per hour, per day.")
    _vocab.update(at=time.time(), text=text)
    return text


def clean(text: str) -> str:
    out = " ".join((text or "").split())
    for pattern, repl in FIXES:
        out = re.sub(pattern, repl, out, flags=re.I)
    if out and out[0].islower():
        out = out[0].upper() + out[1:]
    return out


def transcribe(audio: bytes, filename: str, content_type: str, provider: str, api_key: str,
               model: str = "", language: str = "", base_url: str = "") -> str:
    p = PROVIDERS.get(provider)
    if p is None:
        raise VoiceError("Choose a speech provider in Settings.")
    if not audio:
        raise VoiceError("No audio was recorded.")
    if len(audio) > MAX_BYTES:
        raise VoiceError("The recording is too long. Keep it under a few minutes.")
    url = (base_url or p["base_url"]).rstrip("/")
    if not url:
        raise VoiceError("Set the base URL of the speech endpoint in Settings.")
    if provider != "custom" and not api_key:
        raise VoiceError(f"Add your {p['label']} API key in Settings to use voice input.")
    language = language if language in LANGUAGES else ""
    data = {"model": model or p["model"], "response_format": "json", "temperature": "0", "prompt": vocabulary()}
    if language:
        data["language"] = language
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        resp = net.post(f"{url}/audio/transcriptions", headers=headers, data=data,
                             files={"file": (filename, audio, content_type or "application/octet-stream")}, timeout=120)
    except requests.RequestException as e:
        raise VoiceError(f"Cannot reach {p['label']}: {e.__class__.__name__}") from e
    if resp.status_code != 200:
        detail = ""
        try:
            err = resp.json().get("error", {})
            detail = err.get("message", "") if isinstance(err, dict) else str(err)
        except ValueError:
            detail = resp.text[:200]
        if resp.status_code in (401, 403):
            raise VoiceError(f"The {p['label']} key was rejected for speech. {detail}".strip())
        if resp.status_code == 429:
            raise VoiceError(f"{p['label']} is rate limiting speech requests. Try again in a moment.")
        raise VoiceError(f"{p['label']} could not transcribe the audio ({resp.status_code}). {detail}".strip())
    try:
        body = resp.json()
        text = body.get("text", "") if isinstance(body, dict) else str(body)
    except ValueError:
        text = resp.text
    text = clean(text)
    if not text:
        raise VoiceError("No speech was recognised. Speak a little closer to the microphone and try again.")
    return text
