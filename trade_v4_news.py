#!/Users/clneoh/.hermes/hermes-agent/venv/bin/python3
"""
Economic Calendar — Recurring high-impact events.
Returns bonus scoring and warnings for today's events.
"""

from datetime import datetime, timezone, timedelta
import json

# ── Recurring high-impact events ──
# Format: (name, schedule_rule, gold_impact, stock_impact)
# schedule_rule: "first_friday", "mid_month", "every_6w_from"
FOMC_2026 = [
    "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16"
]

EVENTS = [
    {"name": "NFP", "schedule": "first_friday", "gold": "mixed", "stocks": "bullish_if_strong",
     "impact": "HIGH", "note": "Strong jobs = hawkish Fed = gold↓ stocks↑"},
    {"name": "CPI", "schedule": "mid_month", "gold": "bullish_if_low", "stocks": "bullish_if_low",
     "impact": "HIGH", "note": "Low CPI = dovish = gold↑ stocks↑"},
    {"name": "PPI", "schedule": "mid_month", "gold": "bullish_if_low", "stocks": "bullish_if_low",
     "impact": "MEDIUM", "note": "Producer prices — CPI precursor"},
    {"name": "FOMC", "schedule": "fomc_2026", "gold": "bullish_if_dovish", "stocks": "bullish_if_dovish",
     "impact": "HIGH", "note": "Rate decision — dovish = gold↑ stocks↑"},
    {"name": "GDP", "schedule": "quarterly", "gold": "bullish_if_weak", "stocks": "bearish_if_weak",
     "impact": "HIGH", "note": "Weak GDP = recession fear = gold↑ stocks↓"},
    {"name": "Retail Sales", "schedule": "mid_month", "gold": "mixed", "stocks": "bullish_if_strong",
     "impact": "MEDIUM", "note": "Consumer strength — stocks↑"},
    {"name": "PMI", "schedule": "first_business_day", "gold": "mixed", "stocks": "bullish_if_strong",
     "impact": "MEDIUM", "note": "Manufacturing pulse"},
]


def _is_first_friday(d):
    return d.weekday() == 4 and 1 <= d.day <= 7


def _is_first_business_day(d):
    # First weekday of month
    if d.day <= 3 and d.weekday() < 5:
        return True
    return False


def _is_mid_month(d):
    return 10 <= d.day <= 16


def _is_quarterly(d):
    return d.month in (1, 4, 7, 10) and 15 <= d.day <= 25


GDP_DATES_2026 = [
    "2026-04-30", "2026-07-30", "2026-10-29", "2027-01-28"
]

# ... (FOMC dates remain)

def get_today_events(date=None):
    """Return list of events happening today, with impact and direction bias."""
    if date is None:
        import pytz
        date = datetime.now(pytz.timezone("Asia/Singapore"))

    today_str = date.strftime("%Y-%m-%d")
    events = []

    for ev in EVENTS:
        schedule = ev["schedule"]
        match = False

        if schedule == "fomc_2026":
            if today_str in FOMC_2026:
                match = True
        elif schedule == "first_friday":
            match = _is_first_friday(date)
        elif schedule == "first_business_day":
            match = _is_first_business_day(date)
        elif schedule == "mid_month":
            match = _is_mid_month(date)
        elif schedule == "quarterly":
            match = today_str in GDP_DATES_2026

        if match:
            events.append(ev)

    return events


def news_bonus(events, asset, direction):
    """
    Return bonus points for news alignment.
    asset: 'XAUUSD' or stock ticker
    direction: 'long' or 'short'
    """
    if not events:
        return 0, None

    bonus = 0
    notes = []

    for ev in events:
        bias = ev.get("gold" if asset == "XAUUSD" else "stocks", "mixed")

        # Gold: bullish_if_dovish → long gets bonus, short doesn't
        if "bullish" in bias and direction == "long":
            bonus = max(bonus, 1)
            notes.append(f"{ev['name']}: {bias} → +1")
        elif "bearish" in bias and direction == "short":
            bonus = max(bonus, 1)
            notes.append(f"{ev['name']}: {bias} → +1")

    return bonus, "; ".join(notes) if notes else None


def news_warning(events):
    """Return warning text if high-impact events are happening today."""
    high = [e for e in events if e["impact"] == "HIGH"]
    if high:
        names = ", ".join(e["name"] for e in high)
        return f"⚠️ HIGH IMPACT TODAY: {names} — wait for event before entry"
    medium = [e for e in events if e["impact"] == "MEDIUM"]
    if medium:
        names = ", ".join(e["name"] for e in medium)
        return f"📅 Events today: {names}"
    return None
