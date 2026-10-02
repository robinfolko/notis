#!/usr/bin/env python3
"""Bevakar Bokadirekt efter två lediga tider i följd (dubbelpass)
och skickar push-notis via ntfy.

Körning:
    NTFY_TOPIC=ditt-hemliga-amne python3 bevaka_dubbelpass.py
    NTFY_TOPIC=ditt-hemliga-amne python3 bevaka_dubbelpass.py --test
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

SERVICE_ID = 3075016
PLACE_ID = 57787
EMPLOYEE_ID = 258171
API = "https://www.bokadirekt.se/api/book/{service}/{place}/{ts}/{emp}?reborn=true"

NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
BOOKING_URL = os.environ.get("BOOKING_URL", "")  # valfritt: länk i notisen
STATE_FILE = Path(os.environ.get("STATE_FILE", "seen.json"))
WINDOW_DAYS = int(os.environ.get("WINDOW_DAYS", "21"))  # notis bara inom detta fönster

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36",
    "Accept": "application/json",
}
DAGAR = ["mån", "tis", "ons", "tor", "fre", "lör", "sön"]


def week_start_ms(d: date) -> int:
    """Måndag 00:00 UTC för veckan som innehåller d, i millisekunder."""
    monday = d - timedelta(days=d.weekday())
    dt = datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def fetch_week(ts_ms: int) -> dict:
    url = API.format(service=SERVICE_ID, place=PLACE_ID, ts=ts_ms, emp=EMPLOYEE_ID)
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()["availability"]


def collect_slots() -> dict[datetime, int]:
    """Returnerar {starttid: längd i minuter} för alla lediga tider framåt."""
    today = datetime.now(timezone.utc).date()
    first_ts = week_start_ms(today)
    first = fetch_week(first_ts)

    overview = first.get("fromErpOverview", {}).get("datesWithOpeningHours", [])
    dates = {date.fromisoformat(s[:10]) for s in overview}
    weeks = sorted({week_start_ms(d) for d in dates} | {first_ts})

    slots: dict[datetime, int] = {}
    for ts in weeks:
        data = first if ts == first_ts else fetch_week(ts)
        for s in data.get("fromErp", []):
            slots[datetime.fromisoformat(s["start"])] = s["duration"]
        if ts != first_ts:
            time.sleep(1)  # var snäll mot servern
    return slots


def find_pairs(slots: dict[datetime, int]) -> list[datetime]:
    now = datetime.now(timezone.utc)
    pairs = []
    for start, dur in sorted(slots.items()):
        if start > now and start + timedelta(minutes=dur) in slots:
            pairs.append(start)
    return pairs


def fmt(t: datetime) -> str:
    return f"{DAGAR[t.weekday()]} {t.day}/{t.month} kl. {t:%H:%M}"


def notify(msg: str) -> None:
    if not NTFY_TOPIC:
        print("(NTFY_TOPIC saknas, ingen notis skickad)")
        return
    headers = {"Title": "Dubbelpass ledigt", "Priority": "high", "Tags": "calendar"}
    if BOOKING_URL:
        headers["Click"] = BOOKING_URL
    requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=msg.encode("utf-8"),
                  headers=headers, timeout=20)


def main() -> int:
    test = "--test" in sys.argv
    try:
        slots = collect_slots()
    except Exception as e:  # nätverksfel, ändrat API etc.
        print(f"Fel vid hämtning: {e}", file=sys.stderr)
        return 1

    pairs = find_pairs(slots)
    print(f"{len(slots)} lediga tider, {len(pairs)} dubbelpass möjliga")
    for p in pairs:
        print("  ", fmt(p))

    if test:
        notify(f"Test: {len(slots)} lediga tider, {len(pairs)} dubbelpass.")
        return 0

    current = {p.isoformat() for p in pairs}

    # Första körningen: spara nuläget utan att skicka notiser.
    if not STATE_FILE.exists():
        STATE_FILE.write_text(json.dumps(sorted(current)))
        print("Första körningen: nuläget sparat, inga notiser skickade.")
        return 0

    seen = set(json.loads(STATE_FILE.read_text()))
    cutoff = datetime.now(timezone.utc) + timedelta(days=WINDOW_DAYS)
    new = sorted(p for p in pairs if p.isoformat() not in seen and p <= cutoff)
    if new:
        notify("\n".join(fmt(p) for p in new))

    # Spara bara nuvarande par: försvinner ett par och dyker upp igen
    # (t.ex. ny avbokning) får du en ny notis.
    STATE_FILE.write_text(json.dumps(sorted(current)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
