#!/Users/clneoh/.hermes/hermes-agent/venv/bin/python3
"""
Trade V4 Engine — Config-Driven 8-Step Pipeline
================================================
Reads /tmp/trade_v4_config.json, runs all 8 steps for all assets.
Every rule, threshold, and parameter is config-driven — no hardcoded logic.
"""

import json, os, sys, math, time
from datetime import datetime, timezone
from pathlib import Path

# ── News + Scoring integration ──
sys.path.insert(0, "/tmp")
from trade_v4_fedwatch import get_enhanced_events, enhanced_news_bonus, enhanced_warning, get_fedwatch, fedwatch_bonus
from trade_v4_fundamentals import fetch_fundamentals, fundamentals_bonus
from trade_v4_scoring import score_criterion, SCALING_FUNCTIONS
import pytz

LOCAL_TZ = pytz.timezone("Asia/Singapore")

# ── Config ──────────────────────────────────────────────────
CONFIG_FILE = "trade_v4_config.json"
STATE_FILE = "trade_v4_state.json"
JOURNAL_FILE = "trade_v4_journal.json"
POSITIONS_FILE = "trade_v4_positions.json"
TRADES_LOG = "trade_v4_trades.txt"

# TV symbol mapping from config asset names
TV_MAP = {
    "XAUUSD": ("OANDA", "XAUUSD"),
    "AAPL":   ("NASDAQ", "AAPL"),
    "META":   ("NASDAQ", "META"),
    "GOOGL":  ("NASDAQ", "GOOGL"),
    "MSFT":   ("NASDAQ", "MSFT"),
    "DXY":    ("TVC", "DXY"),
    "VIX":    ("TVC", "VIX"),
    "SPX":    ("SP", "SPX"),
    "US10Y":  ("TVC", "US10Y"),
    "BTC":    ("COINBASE", "BTCUSD"),
}


# ── TV Connection ───────────────────────────────────────────
def connect_tv():
    import tvDatafeed as tv
    return tv.TvDatafeed()


def fetch(tv, sym, ex, interval_str, n=200):
    """Fetch bars. interval_str: '1d','1h','15m'."""
    import tvDatafeed as tv2
    m = {"15m": tv2.Interval.in_15_minute, "1h": tv2.Interval.in_1_hour,
         "1d": tv2.Interval.in_daily, "5m": tv2.Interval.in_5_minute}
    for _ in range(3):
        try:
            df = tv.get_hist(symbol=sym, exchange=ex, interval=m[interval_str], n_bars=n)
            if df is not None and not df.empty:
                break
        except Exception:
            time.sleep(3)
    if df is None or df.empty:
        return None
    return df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                               "close": "Close", "volume": "Volume"})


# ── Helpers ─────────────────────────────────────────────────
def calc_ema(series, period):
    if len(series) < period:
        return None
    return float(series.ewm(span=period, adjust=False).mean().iloc[-1])


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = sum(d for d in deltas[-period:] if d > 0) / period
    losses = sum(abs(d) for d in deltas[-period:] if d < 0) / period
    if losses == 0:
        return 100
    return 100 - 100 / (1 + gains / losses)


def find_swings(df, lookback=3):
    """Find swing highs and lows in dataframe."""
    n = len(df)
    if n < lookback * 2 + 1:
        return [], []
    highs, lows = [], []
    high_vals = df["High"].values
    low_vals = df["Low"].values
    for i in range(lookback, n - lookback):
        is_high = all(high_vals[i] >= high_vals[i - lookback:i]) and all(high_vals[i] >= high_vals[i + 1:i + 1 + lookback])
        is_low  = all(low_vals[i]  <= low_vals[i - lookback:i])  and all(low_vals[i]  <= low_vals[i + 1:i + 1 + lookback])
        if is_high:
            highs.append((i, float(high_vals[i])))
        if is_low:
            lows.append((i, float(low_vals[i])))
    return highs, lows


def count_touches(df, level, tolerance_pct=0.3):
    """Count how many times price touched a level (±tolerance%)."""
    touches = 0
    high = df["High"].values
    low = df["Low"].values
    tol = level * tolerance_pct / 100
    for h, l in zip(high, low):
        if l - tol <= level <= h + tol:
            touches += 1
    return touches


def is_engulfing(df, direction):
    """Check if last candle is engulfing. direction: 'bullish' or 'bearish'."""
    if len(df) < 2:
        return False
    prev_o, prev_c = float(df["Open"].iloc[-2]), float(df["Close"].iloc[-2])
    cur_o, cur_c = float(df["Open"].iloc[-1]), float(df["Close"].iloc[-1])
    if direction == "bullish":
        return cur_c > cur_o and cur_o < prev_c and cur_c > prev_o and prev_c < prev_o
    else:
        return cur_c < cur_o and cur_o > prev_c and cur_c < prev_o and prev_c > prev_o


def is_hammer(df):
    """Check if last candle is a hammer (bullish reversal)."""
    if len(df) < 1:
        return False
    o, c, h, l = [float(df[x].iloc[-1]) for x in ["Open", "Close", "High", "Low"]]
    body = abs(c - o)
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    total_range = h - l
    if total_range == 0:
        return False
    return lower_wick > body * 2 and upper_wick < body * 0.5 and c > o


def atr(df, period=14):
    """Calculate ATR value."""
    if len(df) < period + 1:
        return None
    h, l, c = df["High"].values, df["Low"].values, df["Close"].values
    tr = []
    for i in range(1, len(h)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    if not tr:
        return None
    return float(sum(tr[-period:]) / period)


# ── Step 1: REGIME ──────────────────────────────────────────
def step_regime(cfg, tv):
    indicators = cfg["regime"]["indicators"]
    results = {}
    for name in indicators:
        ex, sym = TV_MAP.get(name, (None, None))
        if not ex:
            continue
        df = fetch(tv, sym, ex, "1d", 50)
        if df is not None and not df.empty:
            close = df["Close"]
            current = float(close.iloc[-1])
            sma20 = float(close.rolling(20).mean().iloc[-1])
            chg_5d = float((close.iloc[-1] / close.iloc[-5] - 1) * 100) if len(close) >= 5 else 0
            results[name] = {"price": round(current, 2), "trend": "UP" if current > sma20 else "DOWN",
                             "chg_5d": round(chg_5d, 2)}
        else:
            results[name] = {"price": None, "trend": "N/A"}

    # Score regime
    score = 0
    reasons = []
    dxy_trend = results.get("DXY", {}).get("trend", "N/A")
    vix_price = results.get("VIX", {}).get("price")
    spx_trend = results.get("SPX", {}).get("trend", "N/A")

    if dxy_trend == "UP":
        reasons.append("DXY↑ (USD strength)")
        score -= 1
    else:
        reasons.append("DXY↓ (USD weakness)")
        score += 1

    if vix_price and vix_price > 25:
        reasons.append(f"VIX {vix_price:.0f} (fear)")
        score -= 1
    elif vix_price:
        reasons.append(f"VIX {vix_price:.0f} (low vol)")
        score += 1

    if spx_trend == "UP":
        reasons.append("SPX↑ (risk-on)")
        score += 1
    else:
        reasons.append("SPX↓ (risk-off)")
        score -= 1

    if score >= 2:
        regime = "RISK-ON"
    elif score <= 0:
        regime = "RISK-OFF"
    else:
        regime = "NEUTRAL"

    return {"status": "PASS", "regime": regime, "score": score,
            "reasons": reasons, "assets": results}


# ── Step 2: CORRELATION ─────────────────────────────────────
def step_correlation(cfg, tv):
    checks = cfg["correlation"]["checks"]
    results = {}
    warnings = []

    # Fetch XAU price
    xau_df = fetch(tv, "XAUUSD", "OANDA", "1d", 30)
    xau_chg = None
    if xau_df is not None and not xau_df.empty:
        xau_chg = float((xau_df["Close"].iloc[-1] / xau_df["Close"].iloc[-5] - 1) * 100)

    for check_name, check in checks.items():
        if not check.get("enabled"):
            continue

        if check_name == "xau_vs_dxy":
            dxy_df = fetch(tv, "DXY", "TVC", "1d", 10)
            if dxy_df is not None and not dxy_df.empty:
                chg = float((dxy_df["Close"].iloc[-1] / dxy_df["Close"].iloc[-5] - 1) * 100)
                results["dxy_5d"] = round(chg, 2)
                if chg > 0.5 and xau_chg is not None and xau_chg > 0:
                    warnings.append(f"DXY +{chg:.2f}% but gold also up — divergence")

        elif check_name == "stocks_vs_spx":
            spx_df = fetch(tv, "SPX", "SP", "1d", 10)
            if spx_df is not None and not spx_df.empty:
                spx_chg = float((spx_df["Close"].iloc[-1] / spx_df["Close"].iloc[-5] - 1) * 100)
                results["spx_5d"] = round(spx_chg, 2)

        elif check_name == "cross_stock_overlap":
            # Flag if all 3 tech stocks moving same direction
            stock_chgs = {}
            for stock in ["AAPL", "META", "GOOGL"]:
                s_df = fetch(tv, stock, "NASDAQ", "1d", 10)
                if s_df is not None and not s_df.empty:
                    stock_chgs[stock] = float((s_df["Close"].iloc[-1] / s_df["Close"].iloc[-5] - 1) * 100)
            if len(stock_chgs) == 3:
                all_up = all(v > 0 for v in stock_chgs.values())
                all_down = all(v < 0 for v in stock_chgs.values())
                if all_up or all_down:
                    warnings.append(f"All 3 tech stocks {'up' if all_up else 'down'} — single sector risk")
                results["stock_chgs"] = {k: round(v, 2) for k, v in stock_chgs.items()}

        elif check_name == "gold_vs_stocks":
            if xau_chg is not None:
                spx_df = fetch(tv, "SPX", "SP", "1d", 25)
                if spx_df is not None and not spx_df.empty:
                    spx_chg_20d = float((spx_df["Close"].iloc[-1] / spx_df["Close"].iloc[-20] - 1) * 100)
                    if xau_chg > 2 and spx_chg_20d < -2:
                        warnings.append("Gold ↑ +2% while SPX ↓ -2% — safe haven bid")
                    elif xau_chg < -2 and spx_chg_20d > 2:
                        warnings.append("Gold ↓ while SPX ↑ — risk-on rotation")

    status = "WARN" if warnings else "PASS"
    return {"status": status, "checks": results, "warnings": warnings}


# ── Step 3: SETUP ───────────────────────────────────────────

def _tf_for(rule):
    """Determine which timeframe a rule needs. Returns 'bias' or 'entry'."""
    r = rule.lower()
    # Daily/biases hints
    if any(k in r for k in ["on daily", "20-day", "20 day", "prior week",
                              "50ema", "20ema"]):
        return "bias"
    # Entry timeframe hints
    if any(k in r for k in ["on 1h", "on 15m", "engulfing", "hammer",
                              "swing", "atr expansion", "1.3x"]):
        return "entry"
    # Default: entry
    return "entry"


def step_setup(cfg, tv):
    """Score all setups for all assets. Uses both bias and entry timeframes."""
    setups_cfg = cfg["setups"]
    all_results = {}
    assets_list = list(setups_cfg.keys())

    # Pre-fetch fundamentals for stocks (cached 2h)
    fundamentals = fetch_fundamentals(assets_list)

    for asset, asset_cfg in setups_cfg.items():
        if not isinstance(asset_cfg, dict):
            continue

        ex, sym = TV_MAP.get(asset, (None, None))
        if not ex:
            continue

        threshold = asset_cfg.get("scoring_threshold", 4)
        threshold_pct = asset_cfg.get("scoring_threshold_pct")
        rsi_gate = asset_cfg.get("rsi_gate", {})
        tfs = cfg["assets"].get(asset, {}).get("timeframes", {})

        # Fetch both timeframes
        bias_tf = tfs.get("bias", "1d")
        entry_tf = tfs.get("entry", "1h")
        df_bias = fetch(tv, sym, ex, bias_tf, 200)
        df_entry = fetch(tv, sym, ex, entry_tf, 500)

        if df_entry is None or df_entry.empty:
            all_results[asset] = {"status": "FAIL", "error": f"No {entry_tf} data"}
            continue

        # ── Pre-compute indicators for BOTH timeframes ──
        def compute_indicators(df, label):
            if df is None or df.empty or len(df) < 20:
                return {}
            c = df["Close"]
            return {
                "price": float(c.iloc[-1]),
                "rsi": calc_rsi(list(c), 14),
                "atr": atr(df, 14),
                "ema12": calc_ema(c, 12),
                "ema20": calc_ema(c, 20),
                "ema50": calc_ema(c, 50),
                "vol_last": float(df["Volume"].iloc[-1]) if "Volume" in df else 0,
                "vol_avg20": float(df["Volume"].iloc[-21:-1].mean()) if "Volume" in df and len(df) > 21 else 0,
                "vol_avg5": float(df["Volume"].iloc[-6:-1].mean()) if "Volume" in df and len(df) > 6 else 0,
                "high_20d": float(df["High"].iloc[-21:-1].max()) if len(df) >= 21 else float(df["High"].iloc[:-1].max()),
                "sw_highs": find_swings(df)[0],
                "sw_lows": find_swings(df)[1],
                "len": len(df),
                "ohlc": df,
                "label": label,
            }

        ind_bias = compute_indicators(df_bias, bias_tf)
        ind_entry = compute_indicators(df_entry, entry_tf)

        # Use entry price as current price
        current_price = ind_entry.get("price", 0)
        asset_signals = []

        # ── Session check for regular-only assets ──
        session_filter = cfg.get("entry", {}).get("session_filter", {}).get(asset, "anytime")
        in_session = True
        session_note = ""
        if session_filter == "regular_only":
            rh = cfg.get("entry", {}).get("regular_hours", {})
            try:
                from datetime import datetime as dt, timezone as tz_module
                import pytz
                tz = pytz.timezone(rh.get("timezone", "America/New_York"))
                now_et = dt.now(tz)
                current_time = now_et.strftime("%H:%M")
                start = rh.get("start", "09:30")
                end = rh.get("end", "16:00")
                in_session = start <= current_time <= end
                if not in_session:
                    session_note = f"Outside regular hours ({current_time} ET, session {start}-{end})"
            except ImportError:
                # Fallback: assume in-session if pytz not available
                in_session = True

        for sig_cfg in asset_cfg.get("signals", []):
            name = sig_cfg["name"]
            sig_type = sig_cfg["type"]
            direction = sig_cfg["direction"]
            criteria = sig_cfg["criteria"]

            score = 0
            gate_pass = True
            details = []
            direction_matched = None

            for crit in criteria:
                scaling = crit.get("scaling")
                if scaling:
                    # ── NEW scaled scoring (0-10 per criterion) ──
                    if scaling == "gate":
                        if "earnings" in crit["rule"].lower():
                            gate_pass = _check_earnings_gate(sym, asset)
                            details.append(f"GATE {'PASS' if gate_pass else 'SKIP'}: earnings check for {asset}")
                        continue

                    max_pts = crit.get("max", 10)
                    pts_scored = 0
                    tf = _tf_for(crit["rule"])
                    ind = ind_bias if tf == "bias" else ind_entry
                    df = ind.get("ohlc")
                    if df is None or df.empty:
                        details.append(f" 0  [{tf}] {crit['rule'][:50]}...")
                        score += 0; continue

                    price = ind.get("price", current_price)
                    rsi_val = ind.get("rsi", 50)
                    atr_val = ind.get("atr")
                    ema20_v = ind.get("ema20")
                    ema50_v = ind.get("ema50")
                    vol_last = ind.get("vol_last", 0)
                    vol_avg20 = ind.get("vol_avg20", 0)
                    sw_highs = ind.get("sw_highs", [])
                    sw_lows = ind.get("sw_lows", [])
                    last_sw_high = sw_highs[-1][1] if sw_highs else None
                    last_sw_low = sw_lows[-1][1] if sw_lows else None
                    high_20d = ind.get("high_20d")

                    # Dispatch to scaling function
                    kwargs = {}
                    if scaling == "swing_break":
                        level = last_sw_high if "high" in crit["rule"].lower() else last_sw_low
                        kwargs = {"price": price, "swing_level": level}
                        if level and price > level: direction_matched = "long"
                        elif level and price < level: direction_matched = "short"
                    elif scaling == "volume":
                        if tf == "bias" and len(df) >= 2:
                            vol_check = float(df["Volume"].iloc[-2])
                            vol_avg = float(df["Volume"].iloc[-22:-2].mean()) if len(df) > 22 else vol_avg20
                        else:
                            vol_check = vol_last; vol_avg = vol_avg20
                        kwargs = {"vol_last": vol_check, "vol_avg": vol_avg}
                    elif scaling == "atr_expand":
                        atr_prev = atr(df.iloc[:-20], 14) if len(df) > 20 else atr_val
                        kwargs = {"atr_now": atr_val, "atr_prev": atr_prev}
                    elif scaling == "touches":
                        level = last_sw_high or last_sw_low
                        tc = count_touches(df, level) if level else 0
                        kwargs = {"touch_count": tc}
                    elif scaling == "ema_proximity":
                        ema = ema50_v if asset == "XAUUSD" else ema20_v
                        kwargs = {"price": current_price, "ema": ema}
                        if ema and current_price > ema: direction_matched = "long"
                        elif ema and current_price < ema: direction_matched = "short"
                    elif scaling == "engulfing":
                        bullish = is_engulfing(df, "bullish")
                        bearish = is_engulfing(df, "bearish")
                        hammer = is_hammer(df)
                        is_eng = bullish or bearish
                        is_strong = (bullish and float(df["Close"].iloc[-1]) > float(df["Open"].iloc[-1]) * 1.01) or \
                                    (bearish and float(df["Close"].iloc[-1]) < float(df["Open"].iloc[-1]) * 0.99)
                        kwargs = {"is_engulfing": is_eng or hammer, "is_strong": is_strong}
                        if bullish or hammer: direction_matched = "long"
                        elif bearish: direction_matched = "short"
                    elif scaling == "rsi_range":
                        kwargs = {"rsi": rsi_val, "lo": 30, "hi": 50}
                        if "30" in crit["rule"]: kwargs.update({"lo": 30, "hi": 45 if "45" in crit["rule"] else 50})
                        elif "40" in crit["rule"]: kwargs.update({"lo": 40, "hi": 50})
                    elif scaling == "rsi_momentum":
                        kwargs = {"rsi": rsi_val, "min_val": 55}
                    elif scaling == "new_high":
                        kwargs = {"price": price, "prev_high": high_20d}
                        if high_20d and price > high_20d: direction_matched = "long"
                    elif scaling == "gap_size":
                        if len(df) >= 2:
                            prev_c = float(df["Close"].iloc[-2])
                            gap = abs(price - prev_c) / prev_c * 100
                            kwargs = {"gap_pct": gap}
                            if price > prev_c: direction_matched = "long"
                            else: direction_matched = "short"
                    elif scaling == "drift_confirm":
                        if len(df) >= 2:
                            prev_c = float(df["Close"].iloc[-2])
                            bar_up = float(df["Close"].iloc[-1]) > float(df["Open"].iloc[-1])
                            kwargs = {"gap_up": price > prev_c, "bar_up": bar_up}

                    pts_scored, matched = score_criterion(scaling, max_pts, **kwargs)
                    score += pts_scored
                    if matched:
                        details.append(f"+{pts_scored:.1f}/{max_pts} [{tf}] {scaling}")
                    else:
                        details.append(f" 0/{max_pts}  [{tf}] {scaling}")

                else:
                    # ── Legacy point-based scoring (backward compat) ──
                    rule = crit["rule"]
                    pts = crit.get("points", crit.get("max", 1))
                    if pts == "gate":
                        if "earnings" in rule.lower():
                            gate_pass = _check_earnings_gate(sym, asset)
                            details.append(f"GATE {'PASS' if gate_pass else 'SKIP'}: earnings")
                        continue
                    tf = _tf_for(rule)
                    ind = ind_bias if tf == "bias" else ind_entry
                    df = ind.get("ohlc")
                    if df is None or df.empty:
                        details.append(f" 0  {rule[:50]}..."); continue
                    price = ind.get("price", current_price)
                    rsi_val = ind.get("rsi", 50)
                    matched = False; score_add = 0; r = rule.lower()
                    if "close beyond prior swing" in r:
                        last_sw = (ind.get("sw_highs", [None])[-1:][0] or [None])[1] if ind.get("sw_highs") else None
                        last_sl = (ind.get("sw_lows", [None])[-1:][0] or [None])[1] if ind.get("sw_lows") else None
                        if last_sw and price > last_sw: score_add=pts; direction_matched="long"; matched=True
                        elif last_sl and price < last_sl: score_add=pts; direction_matched="short"; matched=True
                    elif "volume" in r and ">" in r:
                        mult = _extract_multiplier(r)
                        vl = ind.get("vol_last", 0); va = ind.get("vol_avg20", 0)
                        if vl > va * mult: score_add = pts; matched = True
                    elif "atr" in r and "expand" in r:
                        av = ind.get("atr"); ap = atr(df.iloc[:-20], 14) if len(df)>20 else av
                        if av and ap and av>ap*1.2: score_add=pts; matched=True
                    elif "ema" in r and "within" in r:
                        em = ind.get("ema50") if asset=="XAUUSD" else ind.get("ema20")
                        if em and price: dist=abs(price-em)/price*100
                        if dist<=_extract_pct(r): score_add=pts; matched=True
                    elif "engulfing" in r:
                        if is_engulfing(df,"bullish"): score_add=pts; direction_matched="long"; matched=True
                        elif is_engulfing(df,"bearish"): score_add=pts; direction_matched="short"; matched=True
                    elif "hammer" in r and is_hammer(df): score_add=pts; direction_matched="long"; matched=True
                    elif "rsi" in r:
                        if "-" in r:
                            lo,hi=_extract_range(r)
                            if lo<=rsi_val<=hi: score_add=pts; matched=True
                        elif ">" in r and rsi_val>_extract_numeric(r,">"): score_add=pts; matched=True
                        elif "<" in r and rsi_val<_extract_numeric(r,"<"): score_add=pts; matched=True
                    elif "new" in r and "high" in r:
                        hd = ind.get("high_20d")
                        if hd and price>hd: score_add=pts; direction_matched="long"; matched=True
                    elif "touches" in r:
                        lvl = (ind.get("sw_highs",[None])[-1:][0] or [None])[1]
                        if lvl and count_touches(df,lvl)>=2: score_add=pts; matched=True
                    elif "gap" in r and ">" in r:
                        if len(df)>=2:
                            pc=float(df["Close"].iloc[-2]); g=abs(price-pc)/pc*100
                            if g>_extract_pct(r): score_add=pts; matched=True
                    elif "drift" in r:
                        if len(df)>=2:
                            pc=float(df["Close"].iloc[-2])
                            if (price>pc)==(float(df["Close"].iloc[-1])>float(df["Open"].iloc[-1])): score_add=pts; matched=True
                    score += score_add
                    details.append(f"{'+' if matched else ' '}{score_add} [{tf}] {rule[:50]}...")

            # Direction enforcement
            sig_dir = direction_matched
            if direction != "both" and sig_dir and sig_dir != direction:
                score = 0

            # RSI gate
            rsi_long_min = rsi_gate.get("long_min", 0)
            rsi_short_max = rsi_gate.get("short_max", 100)
            entry_rsi_val = ind_entry.get("rsi", 50)
            if sig_dir == "long" and entry_rsi_val < rsi_long_min:
                score = 0
                details.append(f"RSI gate fail: {entry_rsi_val:.1f} < {rsi_long_min}")
            if sig_dir == "short" and entry_rsi_val > rsi_short_max:
                score = 0
                details.append(f"RSI gate fail: {entry_rsi_val:.1f} > {rsi_short_max}")

            # News + FedWatch bonus
            if sig_dir:
                events_today = get_enhanced_events()
                bonus, note = enhanced_news_bonus(events_today, asset, sig_dir)
                if bonus:
                    score += bonus
                    details.append(f"+{bonus} CALENDAR: {note}")
                # FedWatch bonus (separate from calendar)
                fw_bonus, fw_note = fedwatch_bonus(asset, sig_dir)
                if fw_bonus:
                    score += fw_bonus
                    details.append(f"+{fw_bonus} FEDWATCH: {fw_note}")
                # Fundamentals bonus (stocks only)
                f_bonus, f_note = fundamentals_bonus(asset, sig_dir, fundamentals)
                if f_bonus:
                    score += f_bonus
                    details.append(f"+{f_bonus} FUNDAMENTALS: {f_note}")

            passed = gate_pass and sig_dir is not None
            if passed:
                if threshold_pct:
                    # Calculate max possible from criteria
                    max_possible = sum(c.get("max", c.get("points", 10)) for c in criteria if c.get("scaling") != "gate" and c.get("points") != "gate")
                    if max_possible > 0:
                        score_pct = score / max_possible * 100
                        passed = score_pct >= threshold_pct
                        details.append(f"Score: {score:.1f}/{max_possible} ({score_pct:.0f}%) · threshold {threshold_pct}%")
                else:
                    passed = score >= threshold

            # Calculate max possible and percentage
            max_possible = sum(c.get("max", c.get("points", 10)) for c in criteria if c.get("scaling") != "gate" and c.get("points") != "gate")
            if max_possible == 0: max_possible = sum(c.get("max", c.get("points", 10)) for c in criteria)
            score_pct = (score / max_possible * 100) if max_possible > 0 else 0

            asset_signals.append({
                "setup": name, "type": sig_type, "direction": sig_dir,
                "score": score, "threshold": threshold, "threshold_pct": threshold_pct,
                "max_possible": max_possible, "score_pct": score_pct,
                "passed": passed, "details": details,
            })

        # Result
        passing = [s for s in asset_signals if s["passed"]]
        base = {
            "price": round(current_price, 2),
            "rsi": round(ind_entry.get("rsi", 50), 1),
            "atr": round(ind_entry.get("atr", 0), 2) if ind_entry.get("atr") else None,
            "ema": round(ind_entry.get("ema50") or ind_entry.get("ema20") or 0, 2),
            "sw_high": round(last_sw_high, 2) if (ind_entry.get("sw_highs") or [None])[-1:] and ind_entry["sw_highs"] else None,
            "sw_low": round(last_sw_low, 2) if (ind_entry.get("sw_lows") or [None])[-1:] and ind_entry["sw_lows"] else None,
            "in_session": in_session,
            "session_note": session_note,
        }
        if not in_session:
            all_results[asset] = {**base, "status": "OUTSIDE_HOURS", "signal": None, "all_signals": asset_signals}
        elif passing:
            all_results[asset] = {**base, "status": "SIGNAL", "signal": passing[0], "all_signals": asset_signals}
        else:
            all_results[asset] = {**base, "status": "PASS", "signal": None, "all_signals": asset_signals}

    return {"status": "SIGNAL" if any(v.get("status") == "SIGNAL" for v in all_results.values()) else "PASS",
            "assets": all_results}


def _check_earnings_gate(sym, asset_name):
    """Check if earnings were in the last 24 hours. Uses yfinance calendar."""
    try:
        import yfinance as yf
        ticker_map = {"AAPL": "AAPL", "META": "META", "GOOGL": "GOOGL"}
        ticker = ticker_map.get(asset_name, sym)
        st = yf.Ticker(ticker)
        cal = st.calendar
        if cal is not None and not cal.empty:
            earnings_date = cal.get("Earnings Date")
            if earnings_date is not None:
                # earnings_date is usually a list of dates
                ed = earnings_date[0] if hasattr(earnings_date, '__iter__') and not isinstance(earnings_date, str) else earnings_date
                from datetime import datetime, timezone, timedelta
                if isinstance(ed, (int, float)):
                    ed = datetime.fromtimestamp(ed / 1000, tz=timezone.utc)
                elif hasattr(ed, 'to_pydatetime'):
                    ed = ed.to_pydatetime()
                now = datetime.now(LOCAL_TZ)
                if now - timedelta(hours=24) <= ed <= now + timedelta(hours=24):
                    return True
        # Fallback: check earnings_dates
        edates = st.earnings_dates
        if edates is not None and not edates.empty:
            now = datetime.now(LOCAL_TZ)
            from datetime import timedelta
            recent = edates[edates.index >= (now - timedelta(hours=24)).strftime('%Y-%m-%d')]
            return not recent.empty
    except Exception:
        pass
    return False


def _extract_multiplier(rule):
    import re
    m = re.search(r'>\s*([\d.]+)\s*x', rule)
    return float(m.group(1)) if m else 1.5


def _extract_pct(rule):
    import re
    m = re.search(r'([\d.]+)%', rule)
    return float(m.group(1)) if m else 0.5


def _extract_ema_period(rule):
    import re
    m = re.search(r'(\d+)EMA', rule)
    return int(m.group(1)) if m else None


def _extract_numeric(rule, operator):
    import re
    m = re.search(rf'{re.escape(operator)}\s*([\d.]+)', rule)
    return float(m.group(1)) if m else 0


def _extract_range(rule):
    import re
    m = re.search(r'(\d+)\s*-\s*(\d+)', rule)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 100)


def _extract_period(rule):
    import re
    m = re.search(r'(\d+)-day', rule)
    return int(m.group(1)) if m else None


# ── Step 4: INSTRUMENT ──────────────────────────────────────
def step_instrument(cfg, asset):
    inst = cfg["instrument"].get(asset, {})
    return {"status": "PASS", "primary": inst.get("primary", "?"),
            "note": inst.get("note", "")}


# ── Step 5: SIZE ────────────────────────────────────────────
def step_size(cfg, asset, signal_data):
    sizing = cfg["sizing"]
    if not signal_data or not signal_data.get("signal"):
        return {"status": "SKIP"}

    capital = sizing["capital"]
    risk_pct = sizing["risk_per_trade_pct"] / 100
    risk_amount = capital * risk_pct

    price = signal_data.get("price", 0)
    entry_atr = signal_data.get("atr")

    # SL from ATR
    sl_atr = None
    if entry_atr:
        sl_atr = entry_atr * sizing["atr_multiplier_sl"]

    # Position cap
    if asset == "XAUUSD":
        max_pos = capital * sizing["gold_position_max_pct"] / 100
    else:
        max_pos = capital * sizing["stock_position_max_pct"] / 100

    return {"status": "READY", "capital": capital, "risk_per_trade_pct": sizing["risk_per_trade_pct"],
            "risk_amount": round(risk_amount, 2), "max_position": round(max_pos, 2),
            "atr": round(entry_atr, 2) if entry_atr else None,
            "atr_sl_distance": round(sl_atr, 2) if sl_atr else None}


# ── Step 6: ENTRY ───────────────────────────────────────────
def step_entry(cfg, asset, signal_data):
    entry_cfg = cfg["entry"]
    if not signal_data or not signal_data.get("signal"):
        return {"status": "SKIP"}

    sig = signal_data["signal"]
    direction = sig["direction"]
    price = signal_data["price"]

    return {"status": "READY", "type": entry_cfg["type"],
            "direction": direction, "price": price,
            "rules": entry_cfg["rules"]}


# ── Step 7: STOP + TARGET ───────────────────────────────────
def step_stop_target(cfg, asset, signal_data, tv):
    st_cfg = cfg["stops_and_targets"]
    if not signal_data or not signal_data.get("signal"):
        return {"status": "SKIP"}

    sig = signal_data["signal"]
    direction = sig["direction"]
    price = signal_data["price"]
    sw_high = signal_data.get("sw_high")
    sw_low = signal_data.get("sw_low")
    entry_atr = signal_data.get("atr")

    buffer = st_cfg["sl_structural"]["buffer_pct"] / 100
    atr_mult = st_cfg["sl_atr"]["multiplier"]

    # Structural SL
    if direction == "long" and sw_low:
        sl_struct = sw_low * (1 - buffer)
    elif direction == "short" and sw_high:
        sl_struct = sw_high * (1 + buffer)
    else:
        sl_struct = None

    # ATR SL
    sl_atr = None
    if entry_atr:
        atr_cfg = st_cfg["sl_atr"]
        if atr_cfg.get("method") == "atr_plus_buffer":
            atr_dist = entry_atr + price * atr_cfg["buffer_pct"] / 100
        else:
            atr_dist = entry_atr * atr_cfg.get("multiplier", 1.2)
        if direction == "long":
            sl_atr = price - atr_dist
        else:
            sl_atr = price + atr_dist

    # Tighter of the two
    if sl_struct and sl_atr:
        if direction == "long":
            sl_final = max(sl_struct, sl_atr)  # Higher stop = tighter for long
        else:
            sl_final = min(sl_struct, sl_atr)  # Lower stop = tighter for short
    elif sl_struct:
        sl_final = sl_struct
    elif sl_atr:
        sl_final = sl_atr
    else:
        sl_final = price * 0.98 if direction == "long" else price * 1.02

    # Hard cap
    sl_pct = abs(price - sl_final) / price * 100
    caps = st_cfg["sl_hard_caps"]
    if asset == "XAUUSD":
        max_sl_pct = caps["gold_rps_max_pct"]
    else:
        max_sl_pct = caps["stock_max_pct"]
    if sl_pct > max_sl_pct:
        if direction == "long":
            sl_final = price * (1 - max_sl_pct / 100)
        else:
            sl_final = price * (1 + max_sl_pct / 100)

    # TP
    rps = abs(price - sl_final)
    tp_rr = st_cfg["tp_rr_default"]
    tp = price + rps * tp_rr if direction == "long" else price - rps * tp_rr

    rr = tp_rr

    return {"status": "READY", "sl": round(sl_final, 2), "tp": round(tp, 2),
            "rps": round(rps, 2), "rr": rr,
            "sl_pct": round(abs(price - sl_final) / price * 100, 2),
            "tp_pct": round(abs(tp - price) / price * 100, 2),
            "method": "tighter_of_structural_atr"}


# ── Step 8: JOURNAL ─────────────────────────────────────────
def step_journal(all_steps, pos_events=None, positions=None):
    """Log only new signals and position events. Skip repeated same-signal scans."""
    journal = load_journal()
    setups = all_steps.get("setup", {}).get("assets", {})
    has_pos_events = bool(pos_events)

    # Filter to only NEW signals (not already tracked)
    new_signals = []
    for asset, data in setups.items():
        sig = data.get("signal")
        if not sig:
            continue
        # Skip if already open or pending for this asset+direction
        already_tracked = False
        if positions:
            for p in positions.get("open", []) + positions.get("pending", []):
                if p["asset"] == asset:
                    already_tracked = True
                    break
        if not already_tracked:
            new_signals.append({
                "asset": asset, "setup": sig["setup"],
                "direction": sig["direction"], "score": sig["score"],
                "price": data["price"],
            })

    if not new_signals and not has_pos_events:
        return {"status": "SKIP", "entry_count": len(journal["entries"])}

    entry = {"timestamp": datetime.now(timezone.utc).isoformat(),
             "regime": all_steps.get("regime", {}).get("regime"),
             "signals": new_signals, "events": pos_events or []}

    journal["entries"].append(entry)
    save_journal(journal)
    _append_log(entry)

    return {"status": "LOGGED", "entry_count": len(journal["entries"])}


def load_journal():
    if os.path.exists(JOURNAL_FILE):
        with open(JOURNAL_FILE) as f:
            return json.load(f)
    return {"entries": []}


def save_journal(j):
    with open(JOURNAL_FILE, "w") as f:
        json.dump(j, f, indent=2, default=str)


def _append_log(entry):
    ts = entry["timestamp"][:19].replace("T", " ")
    regime = entry.get("regime", "?")
    signals = entry.get("signals", [])
    with open(TRADES_LOG, "a") as f:
        f.write(f"[{ts}] {regime:8s} | {len(signals)} signals\n")
        for s in signals:
            f.write(f"  {s['asset']:6s} {s['setup']:15s} {s['direction']:5s} score={s['score']} @ ${s['price']:.2f}\n")


# ── Position tracking helpers ─────────────────────────────
# ── Position Tracking (Paper) ───────────────────────────────
def load_positions():
    if os.path.exists(POSITIONS_FILE):
        with open(POSITIONS_FILE) as f:
            return json.load(f)
    return {"open": [], "closed": []}


def save_positions(p):
    with open(POSITIONS_FILE, "w") as f:
        json.dump(p, f, indent=2, default=str)


def track_positions(setups, per_asset, tv=None):
    """Open/update/close paper positions. Limit orders fill on touch, not close."""
    pos = load_positions()
    events = []

    # Fetch entry-TF data for limit fill detection on pending entries
    pending_ohlc = _fetch_pending_ohlc(pos, tv)

    # ── Check pending entries for fill-on-touch ──
    for p in list(pos.get("pending", [])):
        asset = p["asset"]
        ohlc = pending_ohlc.get(asset)
        if ohlc:
            filled = _check_limit_fill(p, ohlc)
            if filled:
                pos.setdefault("open", []).append(p)
                pos["pending"].remove(p)
                events.append(f"🔵 {asset} {p['direction']} FILLED @ ${p['entry']:.2f} (limit touch) | SL ${p['sl']:.2f} | TP ${p['tp']:.2f}")
                continue
        # Check expiry: 3 hours without fill → expire
        created = p.get("created_at", "")
        if created:
            try:
                created_dt = datetime.fromisoformat(created)
                age_minutes = (datetime.now(LOCAL_TZ) - created_dt).total_seconds() / 60
                if age_minutes >= 180:  # 3 hours
                    pos["pending"].remove(p)
                    events.append(f"⏰ {asset} {p['direction']} LIMIT EXPIRED @ ${p['entry']:.2f} — 3h no fill")
                    continue
            except Exception:
                pass
        p["bars_waiting"] = p.get("bars_waiting", 0) + 1
        continue

    # ── Check existing open positions ──
    for p in pos.get("open", []):
        asset = p["asset"]
        asset_data = setups.get("assets", {}).get(asset, {})
        price = asset_data.get("price", 0)
        if not price:
            continue

        direction = p["direction"]
        sl = p["sl"]
        tp = p["tp"]
        entry = p["entry"]

        # Check SL hit
        if direction == "long" and price <= sl:
            pnl = (sl - entry) / abs(p["rps"]) if p.get("rps") else (sl - entry) / entry * 100
            pnl_pct = (sl / entry - 1) * 100
            p["exit"] = sl; p["exit_reason"] = "SL"; p["pnl_r"] = -1.0
            p["pnl_pct"] = round(pnl_pct, 2); p["closed_at"] = datetime.now(LOCAL_TZ).isoformat()
            pos["closed"].append(p)
            pos["open"].remove(p)
            events.append(f"❌ {asset} {direction} STOPPED OUT @ ${sl:.2f} | -1R ({pnl_pct:+.2f}%)")
        elif direction == "short" and price >= sl:
            pnl_pct = (entry / sl - 1) * 100
            p["exit"] = sl; p["exit_reason"] = "SL"; p["pnl_r"] = -1.0
            p["pnl_pct"] = round(pnl_pct, 2); p["closed_at"] = datetime.now(LOCAL_TZ).isoformat()
            pos["closed"].append(p)
            pos["open"].remove(p)
            events.append(f"❌ {asset} {direction} STOPPED OUT @ ${sl:.2f} | -1R ({pnl_pct:+.2f}%)")
        # Check TP hit
        elif direction == "long" and price >= tp:
            rps = p.get("rps", abs(entry - sl))
            pnl_r = round((tp - entry) / rps, 1) if rps else 2.0
            pnl_pct = (tp / entry - 1) * 100
            p["exit"] = tp; p["exit_reason"] = "TP"; p["pnl_r"] = pnl_r
            p["pnl_pct"] = round(pnl_pct, 2); p["closed_at"] = datetime.now(LOCAL_TZ).isoformat()
            pos["closed"].append(p)
            pos["open"].remove(p)
            events.append(f"✅ {asset} {direction} TP HIT @ ${tp:.2f} | +{pnl_r}R ({pnl_pct:+.2f}%)")
        elif direction == "short" and price <= tp:
            rps = p.get("rps", abs(sl - entry))
            pnl_r = round((entry - tp) / rps, 1) if rps else 2.0
            pnl_pct = (entry / tp - 1) * 100
            p["exit"] = tp; p["exit_reason"] = "TP"; p["pnl_r"] = pnl_r
            p["pnl_pct"] = round(pnl_pct, 2); p["closed_at"] = datetime.now(LOCAL_TZ).isoformat()
            pos["closed"].append(p)
            pos["open"].remove(p)
            events.append(f"✅ {asset} {direction} TP HIT @ ${tp:.2f} | +{pnl_r}R ({pnl_pct:+.2f}%)")
        else:
            # Update current P&L
            if direction == "long":
                unrealised_r = (price - entry) / abs(p.get("rps", 1))
            else:
                unrealised_r = (entry - price) / abs(p.get("rps", 1))
            p["unrealised_r"] = round(unrealised_r, 2)
            p["current_price"] = round(price, 2)

    # Check for new signals → create pending limit entries
    for asset, data in setups.get("assets", {}).items():
        if data.get("status") != "SIGNAL":
            continue
        # Don't create if already open or pending for this asset
        if any(p["asset"] == asset for p in pos.get("open", [])):
            continue
        if any(p["asset"] == asset for p in pos.get("pending", [])):
            continue

        signal = data.get("signal")
        if not signal:
            continue

        st = per_asset.get(asset, {}).get("stop_target", {})
        if st.get("status") != "READY":
            continue

        entry_price = data["price"]
        sl = st["sl"]
        tp = st["tp"]
        rps = st.get("rps", abs(entry_price - sl))

        new_pending = {
            "asset": asset,
            "direction": signal["direction"],
            "setup": signal["setup"],
            "score": signal["score"],
            "entry": round(entry_price, 2),
            "sl": round(sl, 2),
            "tp": round(tp, 2),
            "rps": round(rps, 2),
            "rr": st.get("rr", 2.0),
            "created_at": datetime.now(LOCAL_TZ).isoformat(),
            "regime": "",
            "bars_waiting": 0,
        }
        pos.setdefault("pending", []).append(new_pending)
        events.append(f"📝 {asset} {signal['direction']} LIMIT PLACED @ ${entry_price:.2f} (waiting for touch) | SL ${sl:.2f} | TP ${tp:.2f}")

    save_positions(pos)
    return pos, events


def _fetch_pending_ohlc(pos, tv):
    """Fetch recent OHLC for assets with pending limit entries."""
    ohlc = {}
    pending = pos.get("pending", [])
    if not pending or tv is None:
        return ohlc
    for p in pending:
        asset = p["asset"]
        if asset in ohlc:
            continue
        ex, sym = TV_MAP.get(asset, (None, None))
        if not ex:
            continue
        entry_tf = "15m" if asset == "XAUUSD" else "1h"
        df = fetch(tv, sym, ex, entry_tf, 5)
        if df is not None and not df.empty:
            last = df.iloc[-1]
            ohlc[asset] = {
                "high": float(last["High"]),
                "low": float(last["Low"]),
                "close": float(last["Close"]),
            }
    return ohlc


def _check_limit_fill(pending, ohlc):
    """Check if limit order was touched on current candle."""
    direction = pending["direction"]
    limit_price = pending["entry"]

    if direction == "long":
        # Buy limit: fills if price dropped to our level
        return ohlc["low"] <= limit_price
    else:
        # Sell limit: fills if price rose to our level
        return ohlc["high"] >= limit_price
def run_pipeline(cfg, tv=None):
    if tv is None:
        tv = connect_tv()

    # ── Global market-hours gate ──
    rh = cfg.get("entry", {}).get("regular_hours", {})
    gh = cfg.get("entry", {}).get("gold_hours", {})
    session_filter = cfg.get("entry", {}).get("session_filter", {})

    def _in_session(tz_name, start_str, end_str):
        try:
            import pytz
            from datetime import datetime as dt
            tz = pytz.timezone(tz_name)
            now = dt.now(tz)
            current = now.strftime("%H:%M")
            # Handle overnight sessions (end < start, e.g. 20:00-05:00)
            if end_str < start_str:
                return current >= start_str or current <= end_str, current
            return start_str <= current <= end_str, current
        except Exception:
            return True, "?"

    stocks_in, _ = _in_session(rh.get("timezone", "Asia/Singapore"),
                                rh.get("start", "21:30"), rh.get("end", "04:00"))

    # Pre-market: data visible, no trading
    pre_start = cfg.get("entry", {}).get("pre_market_start", {})
    stocks_display, _ = _in_session(pre_start.get("timezone", "Asia/Singapore"),
                                     pre_start.get("start", "16:00"), "04:00")
    stocks_display = stocks_display or stocks_in  # Show during pre-market OR regular

    # Gold: any of multiple sessions active
    gold_in = False
    gold_time = "?"
    for sess in gh.get("sessions", [{"start": "15:00", "end": "00:00"}]):
        active, ct = _in_session(gh.get("timezone", "Asia/Singapore"),
                                  sess.get("start", "15:00"), sess.get("end", "00:00"))
        if active:
            gold_in = True
            gold_time = f"{ct} ({sess.get('name','?')})"
            break
    if not gold_in:
        gold_time = ct if 'ct' in dir() else "?"

    any_open = stocks_in or gold_in

    # Even when closed, keep data alive — but don't hunt for setups
    steps = {}
    steps["_sessions"] = {"stocks": stocks_in, "gold": gold_in, "gold_time": gold_time,
                          "stocks_display": stocks_display}

    # Step 1
    steps["regime"] = step_regime(cfg, tv)

    # Step 2
    steps["correlation"] = step_correlation(cfg, tv)

    # Step 3
    steps["setup"] = step_setup(cfg, tv)

    # Steps 4-7 per asset
    asset_steps = {}
    setup_data = steps["setup"].get("assets", {})
    for asset in cfg["assets"]:
        # Skip out-of-session assets
        sf = session_filter.get(asset, "anytime")
        if sf == "regular_only":
            if not stocks_display:
                asset_steps[asset] = {"instrument": {"status": "OUTSIDE_HOURS"},
                                       "size": {"status": "SKIP"}, "entry": {"status": "SKIP"},
                                       "stop_target": {"status": "SKIP"}}
                continue
            if not stocks_in:
                # Pre-market: show data, no trades
                asset_data = setup_data.get(asset, {})
                asset_data["status"] = "PRE_MARKET"
                asset_data["in_session"] = False
                setup_data[asset] = asset_data
        if sf == "gold_session" and not gold_in:
            asset_steps[asset] = {"instrument": {"status": "OUTSIDE_HOURS"},
                                   "size": {"status": "SKIP"}, "entry": {"status": "SKIP"},
                                   "stop_target": {"status": "SKIP"}}
            # Still show data during brief gaps
            asset_data = setup_data.get(asset, {})
            asset_data["status"] = "GAP"
            asset_data["in_session"] = False
            setup_data[asset] = asset_data
            continue

        asset_data = setup_data.get(asset, {})
        asset_steps[asset] = {
            "instrument": step_instrument(cfg, asset),
            "size": step_size(cfg, asset, asset_data),
            "entry": step_entry(cfg, asset, asset_data),
            "stop_target": step_stop_target(cfg, asset, asset_data, tv),
        }
    steps["per_asset"] = asset_steps

    # ── Position tracking ──
    regime = steps["regime"]["regime"]
    positions, pos_events = track_positions(steps["setup"], asset_steps, tv)
    # Tag new positions with regime
    for p in positions["open"]:
        if not p.get("regime"):
            p["regime"] = regime
    save_positions(positions)
    steps["positions"] = positions
    steps["pos_events"] = pos_events

    # Step 8
    steps["journal"] = step_journal(steps, pos_events, positions)

    # Summary
    has_signal = any(
        data.get("status") == "SIGNAL"
        for data in steps["setup"].get("assets", {}).values()
    )
    all_ok = steps["regime"]["status"] == "PASS"

    summary = {
        "timestamp": datetime.now(LOCAL_TZ).isoformat(),
        "overall": "GO" if all_ok else "CAUTION",
        "signal_active": has_signal,
        "regime": steps["regime"]["regime"],
    }

    result = {"summary": summary, "steps": steps}

    # Save state
    save_state(result)
    return result


def save_state(state):
    # Simplify for JSON
    simple = {
        "timestamp": state["summary"]["timestamp"],
        "overall": state["summary"]["overall"],
        "regime": state["summary"]["regime"],
        "signal_active": state["summary"]["signal_active"],
        "sessions": state["steps"].get("_sessions", {}),
        "steps": state["steps"],
        "positions": state["steps"].get("positions", {"open": [], "closed": []}),
    }
    with open(STATE_FILE, "w") as f:
        json.dump(simple, f, indent=2, default=str)


# ── Output Formatting ───────────────────────────────────────
def format_output(result, compact=False):
    if compact:
        return _format_compact(result)
    return _format_full(result)


def _format_compact(result):
    s = result["summary"]
    steps = result["steps"]

    # Market closed — brief status
    sessions = steps.get("_sessions", {})
    if not sessions.get("stocks") and not sessions.get("gold"):
        pos = steps.get("positions", {})
        open_pos = pos.get("open", [])
        pos_line = ""
        if open_pos:
            parts = []
            for p in open_pos:
                ur = p.get("unrealised_r", 0)
                parts.append(f"{p['asset']} {p['direction']} {ur:+.1f}R")
            pos_line = " | " + " · ".join(parts)
        return f"🔒 MARKET CLOSED{pos_line}"

    lines = [f"📊 V4 · {s['overall']} · {s['regime']} · {s['timestamp'][:19].replace('T',' ')}", ""]

    # Regime
    regime = steps["regime"]
    lines.append(f"🌍 Regime: {regime['regime']} (score {regime['score']})")
    for r in regime.get("reasons", [])[:2]:
        lines.append(f"   {r}")

    # News warning
    events_today = get_enhanced_events()
    for w in enhanced_warning(events_today):
        lines.append(w)

    # Correlation
    corr = steps["correlation"]
    if corr.get("warnings"):
        for w in corr["warnings"]:
            lines.append(f"⚠️ {w}")

    # Setups — show with session status per ticker
    setups = steps["setup"].get("assets", {})
    for asset, data in setups.items():
        in_sess = data.get("in_session", True)
        status_text = data.get("status", "?")

        if data.get("signal"):
            sig = data["signal"]
            sess_tag = "" if in_sess else " [CLOSED]"
            lines.append(f"🎯 {asset}: {sig['setup']} {sig['direction'].upper()} score={sig['score']}/{sig['threshold']} @ ${data['price']:.2f}{sess_tag}")
        elif status_text == "OUTSIDE_HOURS":
            lines.append(f"🔴 {asset}: MARKET CLOSED")
        elif status_text == "PRE_MARKET":
            lines.append(f"🟡 {asset}: ${data['price']:.2f} RSI {data.get('rsi','?')} · PRE-MARKET (no trades)")
        elif status_text == "GAP":
            lines.append(f"⚫ {asset}: ${data['price']:.2f} RSI {data.get('rsi','?')} · session gap")
        else:
            top_score = max((s["score"] for s in data.get("all_signals", [])), default=0)
            lines.append(f"   {asset}: ${data['price']:.2f} RSI {data.get('rsi','?')} · no signal (max score {top_score})")

    # Stop/target for active signals
    per_asset = steps.get("per_asset", {})
    for asset, asset_steps in per_asset.items():
        st = asset_steps.get("stop_target", {})
        if st.get("status") == "READY":
            lines.append(f"🛡️ {asset}: SL ${st['sl']:.2f} TP ${st['tp']:.2f} RR {st['rr']}:1")

    # Position events
    pos = steps.get("positions", {})
    pos_events = steps.get("pos_events", [])
    if pos_events:
        lines.append("")
        for e in pos_events:
            lines.append(e)
    # Active positions
    open_pos = pos.get("open", [])
    if open_pos:
        for p in open_pos:
            ur = p.get("unrealised_r", 0)
            ur_emoji = "🟢" if ur > 0 else ("🔴" if ur < 0 else "⚪")
            lines.append(f"📌 {p['asset']} {p['direction']} @ ${p['entry']:.2f} → ${p['current_price']:.2f} {ur_emoji} {ur:+.1f}R | SL ${p['sl']:.2f} TP ${p['tp']:.2f}")

    lines.append(f"\n📝 Journal: {steps['journal']['entry_count']} entries")
    try:
        with open("tunnel_url.txt") as f:
            url = f.read().strip()
        if url:
            lines.append(f"\n🖥️ https://{url}/trade_v4_dashboard.html")
    except:
        pass
    return "\n".join(lines)


def _format_full(result):
    lines = ["=" * 70, f"  TRADE V4 · {result['summary']['regime']}", "=" * 70, ""]
    lines.append(format_output(result, compact=True))
    return "\n".join(lines)


# ── Load Config ─────────────────────────────────────────────
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    print(f"❌ Config not found: {CONFIG_FILE}", file=sys.stderr)
    sys.exit(1)


# ── Main ────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Trade V4 Engine")
    parser.add_argument("--compact", action="store_true", help="Compact output")
    parser.add_argument("--json-out", action="store_true", help="JSON output")
    args = parser.parse_args()

    cfg = load_config()
    tv = connect_tv()
    result = run_pipeline(cfg, tv)

    if args.json_out:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(format_output(result, compact=args.compact))


if __name__ == "__main__":
    main()
