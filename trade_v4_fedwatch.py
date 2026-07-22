#!/Users/clneoh/.hermes/hermes-agent/venv/bin/python3
"""
FedWatch + Economic Calendar Integration
========================================
- CME FedWatch: live rate hike/cut probabilities
- Enhanced economic calendar with actual/forecast/previous data
"""

import json, os, sys
from datetime import datetime, timezone, timedelta


# ═══════════════════════════════════════════════════════════════
# CME FEDWATCH
# ═══════════════════════════════════════════════════════════════

FEDWATCH_URL = "https://www.cmegroup.com/CmeWS/mvc/InterestRateProbability/Current"
FEDWATCH_CACHE = "/tmp/trade_v4_fedwatch.json"


def get_fedwatch():
    """Fetch CME FedWatch probabilities. Cached 30 min."""
    if os.path.exists(FEDWATCH_CACHE):
        with open(FEDWATCH_CACHE) as f:
            data = json.load(f)
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(data.get("ts", "2000-01-01"))).total_seconds() / 60  # UTC for cache age calc
        if age < 30:
            return data

    try:
        import urllib.request
        req = urllib.request.Request(FEDWATCH_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read())

        # Find next FOMC meeting
        meetings = []
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if isinstance(raw, list):
            for entry in raw:
                date_str = entry.get("date", "")
                if date_str >= today:
                    meetings.append({
                        "date": date_str,
                        "label": entry.get("meeting", "FOMC"),
                        "probs": _parse_fedwatch_probs(entry),
                    })
                    if len(meetings) >= 3:
                        break

        result = {"ts": datetime.now(timezone.utc).isoformat(), "meetings": meetings}
        with open(FEDWATCH_CACHE, "w") as f:
            json.dump(result, f, default=str)
        return result
    except Exception as e:
        return {"error": str(e), "meetings": []}


def _parse_fedwatch_probs(entry):
    """Extract probability data from CME JSON."""
    probs = []
    for contract in entry.get("contracts", []):
        name = contract.get("name", "")
        outcomes = contract.get("outcomes", [])
        for o in outcomes:
            probs.append({
                "rate": o.get("rate", ""),
                "prob": o.get("probability", 0),
                "type": name,
            })
    # Get the most probable outcome
    probs.sort(key=lambda x: x["prob"], reverse=True)
    return probs[:3]


def fedwatch_bonus(asset, direction):
    """Return bonus if FedWatch probabilities align with direction."""
    fw = get_fedwatch()
    if not fw or fw.get("error") or not fw.get("meetings"):
        return 0, None

    next_meeting = fw["meetings"][0]
    probs = next_meeting.get("probs", [])
    if not probs:
        return 0, None

    top = probs[0]
    prob = top.get("prob", 0)
    rate = top.get("rate", "")

    bonus = 0
    notes = []

    # Rate hold = neutral (most common). Rate cut = dovish. Rate hike = hawkish.
    if "CUT" in rate.upper() or "LOWER" in rate.upper():
        if direction == "long":
            # Dovish → gold↑ stocks↑
            bonus = 1
            notes.append(f"FedWatch: {prob}% cut → dovish")
    elif "HIKE" in rate.upper() or "RAISE" in rate.upper():
        if direction == "short":
            bonus = 1
            notes.append(f"FedWatch: {prob}% hike → hawkish")

    # Probability-weighted signal: if >70% probability of a move, it's a strong signal
    if bonus > 0 and prob > 70:
        bonus = 2  # Strong conviction
        notes[-1] += f" ({prob}%) → +2"

    return bonus, "; ".join(notes) if notes else None


# ═══════════════════════════════════════════════════════════════
# ENHANCED ECONOMIC CALENDAR
# ═══════════════════════════════════════════════════════════════

# Key recurring events with expected impacts
EVENTS_DETAILED = {
    "NFP": {
        "schedule": "first_friday", "impact": "HIGH",
        "gold_bullish": "weak", "gold_bearish": "strong",
        "stock_bullish": "strong", "stock_bearish": "weak",
        "note": "Non-Farm Payrolls — king of data releases"
    },
    "CPI": {
        "schedule": "mid_month", "impact": "HIGH",
        "gold_bullish": "low", "gold_bearish": "high",
        "stock_bullish": "low", "stock_bearish": "high",
        "note": "Consumer Price Index — inflation gauge"
    },
    "Core CPI": {
        "schedule": "mid_month", "impact": "HIGH",
        "gold_bullish": "low", "gold_bearish": "high",
        "stock_bullish": "low", "stock_bearish": "high",
        "note": "Core CPI (ex-food/energy)"
    },
    "PPI": {
        "schedule": "mid_month", "impact": "MEDIUM",
        "gold_bullish": "low", "gold_bearish": "high",
        "stock_bullish": "low", "stock_bearish": "high",
        "note": "Producer Price Index — CPI precursor"
    },
    "FOMC": {
        "schedule": "fomc_2026", "impact": "HIGH",
        "gold_bullish": "dovish", "gold_bearish": "hawkish",
        "stock_bullish": "dovish", "stock_bearish": "hawkish",
        "note": "Federal Reserve rate decision"
    },
    "FOMC Minutes": {
        "schedule": "fomc_minutes", "impact": "MEDIUM",
        "gold_bullish": "dovish", "gold_bearish": "hawkish",
        "stock_bullish": "dovish", "stock_bearish": "hawkish",
        "note": "FOMC meeting minutes"
    },
    "GDP": {
        "schedule": "quarterly", "impact": "HIGH",
        "gold_bullish": "weak", "gold_bearish": "strong",
        "stock_bullish": "strong", "stock_bearish": "weak",
        "note": "Gross Domestic Product"
    },
    "Retail Sales": {
        "schedule": "mid_month", "impact": "MEDIUM",
        "gold_bullish": "weak", "gold_bearish": "strong",
        "stock_bullish": "strong", "stock_bearish": "weak",
        "note": "Consumer spending pulse"
    },
    "PMI": {
        "schedule": "first_business_day", "impact": "MEDIUM",
        "gold_bullish": "weak", "gold_bearish": "strong",
        "stock_bullish": "strong", "stock_bearish": "weak",
        "note": "Manufacturing PMI"
    },
    "ISM Services": {
        "schedule": "first_week", "impact": "MEDIUM",
        "gold_bullish": "weak", "gold_bearish": "strong",
        "stock_bullish": "strong", "stock_bearish": "weak",
        "note": "Services sector — majority of US economy"
    },
    "Initial Jobless Claims": {
        "schedule": "every_thursday", "impact": "MEDIUM",
        "gold_bullish": "high", "gold_bearish": "low",
        "stock_bullish": "low", "stock_bearish": "high",
        "note": "Weekly unemployment claims"
    },
    "JOLTS": {
        "schedule": "first_week", "impact": "MEDIUM",
        "gold_bullish": "weak", "gold_bearish": "strong",
        "stock_bullish": "strong", "stock_bearish": "weak",
        "note": "Job openings — labor market health"
    },
    "Consumer Confidence": {
        "schedule": "last_tuesday", "impact": "MEDIUM",
        "gold_bullish": "weak", "gold_bearish": "strong",
        "stock_bullish": "strong", "stock_bearish": "weak",
        "note": "Conference Board consumer sentiment"
    },
    "Michigan Sentiment": {
        "schedule": "mid_month", "impact": "MEDIUM",
        "gold_bullish": "weak", "gold_bearish": "strong",
        "stock_bullish": "strong", "stock_bearish": "weak",
        "note": "U of Michigan consumer sentiment"
    },
}


FOMC_2026_DATES = [
    "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16"
]

# FOMC minutes ~3 weeks after each meeting
FOMC_MINUTES_2026 = [
    "2026-02-18", "2026-04-08", "2026-05-27", "2026-07-08",
    "2026-08-19", "2026-10-07", "2026-11-25",
]


GDP_DATES_2026 = [
    "2026-04-30", "2026-07-30", "2026-10-29", "2027-01-28"  # Q1-Q4 Advance GDP
]

# ... (FOMC dates remain)

def get_enhanced_events(date=None):
    """Return all economic events happening today with detailed impact data."""
    if date is None:
        import pytz
        date = datetime.now(pytz.timezone("Asia/Singapore"))
    today_str = date.strftime("%Y-%m-%d")
    events = []

    for name, ev in EVENTS_DETAILED.items():
        schedule = ev["schedule"]
        match = False

        if schedule == "fomc_2026":
            match = today_str in FOMC_2026_DATES
        elif schedule == "fomc_minutes":
            match = today_str in FOMC_MINUTES_2026
        elif schedule == "first_friday":
            match = date.weekday() == 4 and 1 <= date.day <= 7
        elif schedule == "first_business_day":
            match = date.day <= 3 and date.weekday() < 5
        elif schedule == "first_week":
            match = date.day <= 7 and date.weekday() < 5
        elif schedule == "mid_month":
            match = 10 <= date.day <= 16
        elif schedule == "quarterly":
            match = today_str in GDP_DATES_2026
        elif schedule == "every_thursday":
            match = date.weekday() == 3
        elif schedule == "last_tuesday":
            match = date.weekday() == 1 and date.day >= 24

        if match:
            events.append({"name": name, **ev})

    return events


def enhanced_news_bonus(events, asset, direction):
    """Bonus based on detailed event direction mapping."""
    if not events:
        return 0, None

    bonus = 0
    notes = []

    for ev in events:
        if asset == "XAUUSD":
            # Gold wants weak data (dovish Fed) for long, strong data for short
            if direction == "long" and ev.get("gold_bullish") in ("weak", "low", "dovish"):
                bonus = max(bonus, 1)
                notes.append(f"{ev['name']}: {ev['gold_bullish']} data → gold↑")
            elif direction == "short" and ev.get("gold_bearish") in ("strong", "high", "hawkish"):
                bonus = max(bonus, 1)
                notes.append(f"{ev['name']}: {ev['gold_bearish']} data → gold↓")
        else:
            # Stocks want strong data for long
            if direction == "long" and ev.get("stock_bullish") in ("strong", "low", "dovish"):
                bonus = max(bonus, 1)
                notes.append(f"{ev['name']}: {ev['stock_bullish']} data → stocks↑")
            elif direction == "short" and ev.get("stock_bearish") in ("weak", "high", "hawkish"):
                bonus = max(bonus, 1)
                notes.append(f"{ev['name']}: {ev['stock_bearish']} data → stocks↓")

    return bonus, "; ".join(notes) if notes else None


def enhanced_warning(events):
    """Return warning text for high-impact events with timing."""
    high = [e for e in events if e["impact"] == "HIGH"]
    medium = [e for e in events if e["impact"] == "MEDIUM"]

    warnings = []
    if high:
        names = ", ".join(e["name"] for e in high)
        note_texts = [e["note"] for e in high if e.get("note")]
        note = f" ({note_texts[0]})" if note_texts else ""
        warnings.append(f"⚠️ HIGH IMPACT: {names}{note} — wait for release")

    if medium:
        names = ", ".join(e["name"] for e in medium)
        warnings.append(f"📅 {names}")

    return warnings
