"""Fetch the Sport 5 broadcast sheet and write schedule.json for the static page.

Usage: python build.py [output.json]      (default: _site/schedule.json)
Python standard library only.
"""
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

DAYS_AHEAD = 6  # Sport 5 publishes ~6 days ahead
TZ = ZoneInfo("Asia/Jerusalem")

# Type="" is the "ישירים" tab: live broadcasts on all Sport 5 channels + Charlton (Sport 1/2/3...)
SOURCE_URL = "https://www.sport5.co.il/Ajax/GetBroadcastSheetData.aspx?Type=&Date={date}"

# Channel names as they appear in the feed -> display name
CHANNEL_RENAME = {"ערוץ הספורט": "ספורט 5"}

# Sport classification: first matching rule wins (substring match on the title).
# Order matters: "ליגת העל בכדורעף" must hit "other" before "ליגת העל" hits football.
CATEGORY_RULES = [
    ("studio", ["אולפן", "חדשות הספורט", "חמש באוויר", "מסע\"ת", "TOP 10", "מהדורה", "דוקו", "פודקאסט", "תכנית", "תוכנית",
                "יציע העיתונות"]),
    ("other", ["כדורעף", "כדוריד", "טניס", "הוקי", "NHL", "MLB", "בייסבול", "פוטבול", "NFL", "UFC", "MMA",
               "אגרוף", "אופניים", "אתלטיקה", "שחייה", "פורמולה", "MotoGP", "ג'ודו", "גולף", "סנוקר",
               "פאדל", "התעמלות", "פוצ'יוולי", "מרתון", "WWE"]),
    ("basketball", ["כדורסל", "יורוליג", "יורוקאפ", "NBA", "WNBA", "ווינר סל", "פיינל פור"]),
    ("football", ["כדורגל", "ליגת האלופות", "ליגה אירופית", "הליגה האירופית", "קונפרנס", "ליגת העל",
                  "ליגה לאומית", "הליגה הלאומית", "גביע המדינה", "גביע הטוטו", "פרמייר ליג", "ליגה אנגלית",
                  "צ'מפיונשיפ", "לה ליגה", "ליגה ספרדית", "סרייה", "ליגה איטלקית", "בונדסליגה",
                  "ליגה גרמנית", "ליגה צרפתית", "ליג 1", "MLS", "ליגת האומות", "מונדיאל", "יורו",
                  "נבחרת ישראל", "גביע אנגלי", "קופה"]),
]


def classify(title):
    for category, words in CATEGORY_RULES:
        if any(w in title for w in words):
            return category
    return "other"


class SheetParser(HTMLParser):
    """Turns the feed's HTML table into [(channel, 'HH:MM', title), ...] in source order."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self.channel = None
        self.in_header = False
        self.cell = None  # "date" | "text" | "chtext" | None
        self.buf = ""
        self.time = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "") or ""
        if tag == "tr":
            self.in_header = "tr-header" in cls
            self.time = ""
        elif self.in_header and tag == "img" and a.get("alt"):
            self.channel = a["alt"].strip()
        elif self.in_header and tag == "span" and "channel-text" in cls:
            self.cell, self.buf = "chtext", ""
        elif tag == "td" and cls in ("date", "text"):
            self.cell, self.buf = cls, ""

    def handle_endtag(self, tag):
        if self.cell == "chtext" and tag == "span":
            self.channel = self.buf.strip()
            self.cell = None
        elif self.cell and tag == "td":
            text = " ".join(self.buf.split())
            if self.cell == "date":
                self.time = text
            elif self.cell == "text" and text and self.time and self.channel:
                self.rows.append((self.channel, self.time, text))
            self.cell = None

    def handle_data(self, data):
        if self.cell:
            self.buf += data


def parse_day(html, day):
    """Parse one day's feed. Times that go backwards within a channel roll over to the next day."""
    p = SheetParser()
    p.feed(html)
    items, last = [], {}
    for channel, hhmm, title in p.rows:
        try:
            h, m = (int(x) for x in hhmm.split(":"))
        except ValueError:
            continue
        mins = h * 60 + m
        prev_mins, offset = last.get(channel, (-1, 0))
        if mins < prev_mins:
            offset += 1
        last[channel] = (mins, offset)
        start = datetime(day.year, day.month, day.day, h, m) + timedelta(days=offset)
        items.append({"channel": CHANNEL_RENAME.get(channel, channel), "start": start, "title": title})
    return items


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (personal TV guide)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def build_schedule():
    today = datetime.now(TZ).date()
    seen, items, errors = set(), [], []
    for i in range(DAYS_AHEAD + 1):
        day = today + timedelta(days=i)
        try:
            html = fetch(SOURCE_URL.format(date=f"{day.year}-{day.month}-{day.day}"))
        except Exception as e:  # keep going; one bad day shouldn't drop the rest
            errors.append(f"{day}: {e}")
            continue
        for it in parse_day(html, day):
            key = (it["channel"], it["start"], it["title"])
            if key not in seen:
                seen.add(key)
                items.append(it)
        time.sleep(1)  # be polite

    # End time = next programme on the same channel (if within 4h), else start + 2h
    items.sort(key=lambda x: (x["channel"], x["start"]))
    for a, b in zip(items, items[1:] + [None]):
        nxt = b["start"] if b and b["channel"] == a["channel"] else None
        a["end"] = nxt if nxt and nxt - a["start"] <= timedelta(hours=4) else a["start"] + timedelta(hours=2)

    items.sort(key=lambda x: (x["start"], x["channel"]))
    out = [{
        "channel": x["channel"],
        "start": x["start"].strftime("%Y-%m-%dT%H:%M"),
        "end": x["end"].strftime("%Y-%m-%dT%H:%M"),
        "title": x["title"],
        "category": classify(x["title"]),
    } for x in items]
    return {"updated": datetime.now(TZ).strftime("%Y-%m-%dT%H:%M"), "errors": errors, "items": out}


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "_site/schedule.json")
    data = build_schedule()
    print(f"{len(data['items'])} items, errors: {data['errors']}")
    if not data["items"]:
        sys.exit("No items fetched - not publishing (the previous site stays online).")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
