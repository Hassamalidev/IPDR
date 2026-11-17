import pytest
from fastapi.testclient import TestClient

from ipdr import main, voice
from ipdr.voice import VoiceError, clean, transcribe


@pytest.mark.parametrize("heard,expected", [
    ("most used apps tick tock in karachi", "Most used apps TikTok in karachi"),
    ("whats app users in lahore", "WhatsApp users in lahore"),
    ("what's up users yesterday", "WhatsApp users yesterday"),
    ("what's up with the data", "What's up with the data"),
    ("you tube and snap chat traffic", "YouTube and Snapchat traffic"),
    ("i m o calls per hour", "IMO calls per hour"),
    ("top apps in d h a karachi", "Top apps in DHA karachi"),
    ("  show   me   chat g p t usage ", "Show me ChatGPT usage"),
])
def test_name_fixes(heard, expected):
    assert clean(heard) == expected


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def static_vocab(monkeypatch):
    monkeypatch.setitem(voice._vocab, "text", "VOCAB: TikTok, Karachi")
    monkeypatch.setitem(voice._vocab, "at", 9e18)


def test_openai_request(monkeypatch):
    seen = {}

    def fake_post(url, headers, data, files, timeout):
        seen.update(url=url, headers=headers, data=data, files=files)
        return Resp(200, {"text": "most used apps in karachi last week"})

    monkeypatch.setattr(voice.net, "post", fake_post)
    text = transcribe(b"RIFFdata", "speech.webm", "audio/webm", "openai", "sk-x", language="en")
    assert text == "Most used apps in karachi last week"
    assert seen["url"] == "https://api.openai.com/v1/audio/transcriptions"
    assert seen["headers"] == {"Authorization": "Bearer sk-x"}
    assert seen["data"]["model"] == "gpt-4o-transcribe" and seen["data"]["temperature"] == "0"
    assert seen["data"]["language"] == "en" and seen["data"]["prompt"] == "VOCAB: TikTok, Karachi"
    assert seen["files"]["file"] == ("speech.webm", b"RIFFdata", "audio/webm")


def test_groq_and_custom(monkeypatch):
    urls = []
    monkeypatch.setattr(voice.net, "post", lambda url, **k: urls.append((url, k["data"], k["headers"])) or Resp(200, {"text": "hi"}))
    transcribe(b"x", "a.webm", "audio/webm", "groq", "gsk")
    transcribe(b"x", "a.webm", "audio/webm", "custom", "", base_url="http://10.0.0.5:9000/v1/", model="large-v3")
    assert urls[0][0] == "https://api.groq.com/openai/v1/audio/transcriptions" and urls[0][1]["model"] == "whisper-large-v3"
    assert "language" not in urls[0][1]
    assert urls[1][0] == "http://10.0.0.5:9000/v1/audio/transcriptions" and urls[1][1]["model"] == "large-v3"
    assert urls[1][2] == {}


@pytest.mark.parametrize("status,match", [(401, "rejected"), (429, "rate limiting"), (500, "could not transcribe")])
def test_errors(monkeypatch, status, match):
    monkeypatch.setattr(voice.net, "post", lambda *a, **k: Resp(status, {"error": {"message": "x"}}))
    with pytest.raises(VoiceError, match=match):
        transcribe(b"x", "a.webm", "audio/webm", "openai", "k")


def test_input_checks(monkeypatch):
    with pytest.raises(VoiceError, match="API key"):
        transcribe(b"x", "a.webm", "audio/webm", "openai", "")
    with pytest.raises(VoiceError, match="No audio"):
        transcribe(b"", "a.webm", "audio/webm", "openai", "k")
    with pytest.raises(VoiceError, match="base URL"):
        transcribe(b"x", "a.webm", "audio/webm", "custom", "")
    monkeypatch.setattr(voice.net, "post", lambda *a, **k: Resp(200, {"text": "  "}))
    with pytest.raises(VoiceError, match="No speech"):
        transcribe(b"x", "a.webm", "audio/webm", "openai", "k")


def test_api_endpoint(monkeypatch):
    monkeypatch.setattr(voice.net, "post", lambda *a, **k: Resp(200, {"text": "tick tock users"}))
    with TestClient(main.app) as c:
        r = c.post("/api/voice/transcribe", headers={"X-LLM-Key": "k"},
                   files={"audio": ("speech.webm", b"abc", "audio/webm")}, data={"provider": "openai", "language": "en"})
        assert r.status_code == 200 and r.json() == {"text": "TikTok users"}
        bad = c.post("/api/voice/transcribe", files={"audio": ("speech.webm", b"abc", "audio/webm")}, data={"provider": "openai"})
        assert bad.status_code == 400 and "API key" in bad.json()["detail"]
        assert c.post("/api/voice/clean", json={"text": "whats app users"}).json() == {"text": "WhatsApp users"}
