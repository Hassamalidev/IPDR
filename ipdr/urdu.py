from __future__ import annotations

import re

GLOSSARY = {
    "اسلام آباد": "islamabad", "راولپنڈی": "rawalpindi", "پنڈی": "rawalpindi", "لاہور": "lahore", "کراچی": "karachi",
    "فیصل آباد": "faisalabad", "ملتان": "multan", "پشاور": "peshawar", "کوئٹہ": "quetta", "حیدرآباد": "hyderabad",
    "حیدر آباد": "hyderabad", "گوجرانوالہ": "gujranwala", "سیالکوٹ": "sialkot", "سرگودھا": "sargodha",
    "بہاولپور": "bahawalpur", "سکھر": "sukkur", "ایبٹ آباد": "abbottabad", "مردان": "mardan", "گجرات": "gujrat",
    "ساہیوال": "sahiwal", "جہلم": "jhelum", "مظفرآباد": "muzaffarabad", "پاکستان": "pakistan", "پنجاب": "punjab",
    "سندھ": "sindh",
    "پی ٹی سی ایل": "ptcl", "نیا ٹیل": "nayatel", "نیاٹیل": "nayatel", "زونگ": "zong", "جاز": "jazz",
    "ٹیلی نار": "telenor", "ٹیلینار": "telenor", "یوفون": "ufone", "سٹارم فائبر": "stormfiber", "وائی ٹرائب": "witribe",
    "سم": "sim",
    "واٹس ایپ": "whatsapp", "واٹس اپ": "whatsapp", "وٹس ایپ": "whatsapp", "ٹک ٹاک": "tiktok", "ٹِک ٹاک": "tiktok",
    "یوٹیوب": "youtube", "یو ٹیوب": "youtube", "فیس بک": "facebook", "انسٹاگرام": "instagram", "سنیپ چیٹ": "snapchat",
    "ٹیلیگرام": "telegram", "ٹیلی گرام": "telegram", "ٹویٹر": "twitter", "نیٹ فلکس": "netflix", "گوگل": "google",
    "وی پی این": "vpn", "پب جی": "pubg", "فری فائر": "freefire", "امو": "imo", "ایمو": "imo",
    "فحش": "porn", "گندی ویڈیو": "porn videos", "ڈیٹنگ": "dating", "جوا": "gambling", "گیمز": "games", "گیم": "game",
    "سوشل میڈیا": "social media", "ویڈیو": "video", "ویڈیوز": "videos", "موسیقی": "music", "گانے": "songs",
    "پچھلے ہفتے": "last week", "گزشتہ ہفتے": "last week", "پچھلا ہفتہ": "last week", "اس ہفتے": "this week",
    "پچھلے مہینے": "last month", "اس مہینے": "this month",
    "کل تعداد": "total number", "کل صارفین": "total users", "کل ڈیٹا": "total data", "کل ٹریفک": "total traffic",
    "کل": "yesterday", "آج": "today", "پرسوں": "day before yesterday",
    "رات": "night", "صبح": "morning", "شام": "evening", "روزانہ": "daily", "گھنٹہ": "hour", "گھنٹے": "hours",
    "ہفتہ وار": "weekly",
    "سب سے زیادہ": "most", "سب سے کم": "least", "زیادہ": "most", "کم": "least", "ٹاپ": "top", "مقبول": "popular",
    "کتنے": "how many", "کتنی": "how many", "کتنا": "how much", "کون سی": "which", "کونسی": "which", "کون سا": "which",
    "کون": "who", "کس نے": "who", "کہاں": "where", "کب": "when", "کیا": "what",
    "صارفین": "users", "صارف": "user", "لوگ": "people", "لوگوں": "people", "سبسکرائبر": "subscribers",
    "ڈیٹا": "data", "ٹریفک": "traffic", "استعمال": "use", "ایپس": "apps", "ایپ": "app", "ایپلیکیشن": "app",
    "ویب سائٹ": "website", "ویب سائٹس": "websites", "سائٹس": "sites", "سائٹ": "site", "بلاک": "blocked",
    "پتہ": "address", "پتا": "address", "نمبر": "number", "شہر": "city", "شہروں": "cities", "علاقہ": "area",
    "اپلوڈ": "upload", "ڈاؤن لوڈ": "download", "میں": "in", "کے": "", "کی": "", "کا": "", "اور": "and", "یا": "or",
}
_KEYS = sorted(GLOSSARY, key=len, reverse=True)
_URDU = re.compile("[؀-ۿ]")
_WORD_END = "(?![؀-ۿ])"
_WORD_START = "(?<![؀-ۿ])"
_PATTERN = re.compile(_WORD_START + "(" + "|".join(re.escape(k) for k in _KEYS) + ")" + _WORD_END)


def has_urdu(text: str) -> bool:
    return bool(_URDU.search(text or ""))


def to_latin(text: str) -> str:
    out = _PATTERN.sub(lambda m: f" {GLOSSARY[m.group(1)]} ", text or "")
    out = _URDU.sub(" ", out)
    return " ".join(out.split())


def with_latin(question: str) -> str:
    if not has_urdu(question):
        return question
    latin = to_latin(question)
    return f"{question} (in English words: {latin})" if latin else question
