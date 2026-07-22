#!/Users/clneoh/.hermes/hermes-agent/venv/bin/python3
"""
Ticker-specific fundamentals — analyst ratings, targets, gaps.
Adds bonus scoring for stock setups.
"""

import json, os
from datetime import datetime, timezone

CACHE_FILE = "/tmp/trade_v4_fundamentals.json"
CACHE_TTL_MINUTES = 120  # Refresh every 2 hours


def _load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            data = json.load(f)
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(data.get("ts", "2000-01-01T00:00:00"))).total_seconds() / 60
        if age < CACHE_TTL_MINUTES:
            return data.get("fundamentals", {}), None
    return {}, "stale"


def _save_cache(fundamentals):
    with open(CACHE_FILE, "w") as f:
        json.dump({"ts": datetime.now(timezone.utc).isoformat(), "fundamentals": fundamentals}, f, default=str)


def fetch_fundamentals(assets):
    """Fetch analyst ratings, targets, gaps for given assets. Cached 2h."""
    cached, status = _load_cache()
    if status is None and all(a in cached for a in assets):
        return cached

    try:
        import yfinance as yf
    except ImportError:
        return cached or {}

    for asset in assets:
        if asset == "XAUUSD":
            continue
        try:
            ticker = yf.Ticker(asset)
            info = ticker.info
            current = info.get("currentPrice") or info.get("regularMarketPrice") or 0

            cached[asset] = {
                "current": round(current, 2) if current else None,
                "target_mean": round(info.get("targetMeanPrice", 0), 2) or None,
                "target_high": round(info.get("targetHighPrice", 0), 2) or None,
                "target_low": round(info.get("targetLowPrice", 0), 2) or None,
                "recommendation": info.get("recommendationKey", ""),
                "recommendation_mean": info.get("recommendationMean"),
                "analysts": info.get("numberOfAnalystOpinions"),
                "short_pct": info.get("shortPercentOfFloat"),
            }
        except Exception:
            cached[asset] = {"error": "fetch failed"}

    _save_cache(cached)
    return cached


def fundamentals_bonus(asset, direction, fundamentals=None):
    """
    Return bonus points based on analyst consensus.
    +1 if signal direction aligns with fundamentals.
    """
    if not fundamentals or asset not in fundamentals:
        return 0, None

    f = fundamentals[asset]
    if f.get("error"):
        return 0, None

    bonus = 0
    notes = []

    # Target price upside
    current = f.get("current")
    target = f.get("target_mean")
    if current and target and target > 0:
        upside_pct = (target - current) / current * 100
        if direction == "long" and upside_pct > 5:
            bonus = max(bonus, 1)
            notes.append(f"Target +{upside_pct:.0f}% upside → +1")
        elif direction == "short" and upside_pct < -5:
            bonus = max(bonus, 1)
            notes.append(f"Target {upside_pct:.0f}% downside → +1")

    # Strong analyst consensus
    rec = f.get("recommendation", "")
    rec_mean = f.get("recommendation_mean")
    if direction == "long" and rec in ("buy", "strong_buy"):
        bonus = max(bonus, 1)
        notes.append(f"{rec.replace('_',' ').title()} ({rec_mean}) → +1")
    elif direction == "short" and rec in ("sell", "strong_sell"):
        bonus = max(bonus, 1)
        notes.append(f"{rec.replace('_',' ').title()} ({rec_mean}) → +1")

    # High short interest (contrarian for long, confirm for short)
    short_pct = f.get("short_pct")
    if short_pct:
        if direction == "short" and short_pct > 10:
            bonus = max(bonus, 1)
            notes.append(f"Short float {short_pct:.0f}% → +1")
        elif direction == "long" and short_pct > 15:
            # High short interest + long = squeeze potential
            bonus = max(bonus, 1)
            notes.append(f"Short float {short_pct:.0f}% (squeeze potential) → +1")

    return bonus, "; ".join(notes) if notes else None
