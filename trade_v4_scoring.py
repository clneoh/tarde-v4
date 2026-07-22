# ── Linear Scaled Scoring Functions ──────────────────────────
# Each returns 0–max points based on measured intensity

def scale_swing_break(price, swing_level, max_pts=10):
    """How far price broke beyond swing. 0.1% = 2pts, 0.5%+ = max."""
    if not swing_level or swing_level <= 0:
        return 0
    pct = abs(price - swing_level) / swing_level * 100
    if pct <= 0:
        return 0
    # Linear: 0%→0pts, 0.5%→max
    return round(min(pct / 0.5 * max_pts, max_pts), 1)


def scale_volume(vol_last, vol_avg, max_pts=10):
    """Volume vs average. 1x=0pts, 3x=max."""
    if vol_avg <= 0:
        return 0
    ratio = vol_last / vol_avg
    return round(min((ratio - 1) / 2 * max_pts, max_pts), 1)


def scale_atr_expand(atr_now, atr_prev, max_pts=10):
    """ATR expansion. 1x=0pts, 2.5x=max."""
    if not atr_prev or atr_prev <= 0:
        return 0
    ratio = atr_now / atr_prev
    return round(min((ratio - 1) / 1.5 * max_pts, max_pts), 1)


def scale_touches(touch_count, max_pts=10):
    """Number of prior touches at level. 1=2pts, 5+=max."""
    return round(min((touch_count - 1) / 4 * max_pts, max_pts), 1)


def scale_ema_proximity(price, ema, max_pts=10):
    """How close to EMA. 1% away=0pts, at EMA=max."""
    if not ema or ema <= 0:
        return 0
    dist = abs(price - ema) / price * 100
    return round(max(max_pts - (dist / 1.0 * max_pts), 0), 1)


def scale_engulfing(is_engulfing, is_strong=False, max_pts=10):
    """Engulfing quality. None=0, standard=6, strong=max."""
    if not is_engulfing:
        return 0
    return max_pts if is_strong else round(max_pts * 0.6, 1)


def scale_rsi_range(rsi, lo, hi, max_pts=10):
    """RSI in ideal range. Center=10, edges=2."""
    if lo <= rsi <= hi:
        center = (lo + hi) / 2
        half = (hi - lo) / 2
        dist = abs(rsi - center)
        return round(max(max_pts - (dist / half * max_pts), 2), 1)
    return 0


def scale_rsi_momentum(rsi, min_val, max_pts=10):
    """RSI for momentum. Below min=0, at max_ideal=max."""
    max_ideal = min_val + 20  # e.g., 55→75 is ideal range
    if rsi < min_val:
        return 0
    return round(min((rsi - min_val) / 20 * max_pts, max_pts), 1)


def scale_new_high(price, prev_high, max_pts=10):
    """How far above prior 20d high. Breaking=3, 3% above=max."""
    if not prev_high or prev_high <= 0:
        return 0
    pct = (price - prev_high) / prev_high * 100
    if pct <= 0:
        return 0
    return round(min(pct / 3.0 * max_pts, max_pts), 1)


def scale_gap_size(gap_pct, max_pts=10):
    """Gap size. 0.5%=2pts, 5%+=max."""
    if gap_pct <= 0:
        return 0
    return round(min(gap_pct / 5.0 * max_pts, max_pts), 1)


def scale_drift_confirm(gap_up, bar_up, max_pts=10):
    """Gap direction confirmed by drift. Confirmed=max, not=0."""
    return max_pts if gap_up == bar_up else 0


# ── Scoring dispatcher ──────────────────────────────────────
SCALING_FUNCTIONS = {
    "swing_break": scale_swing_break,
    "volume": scale_volume,
    "atr_expand": scale_atr_expand,
    "touches": scale_touches,
    "ema_proximity": scale_ema_proximity,
    "engulfing": scale_engulfing,
    "rsi_range": scale_rsi_range,
    "rsi_momentum": scale_rsi_momentum,
    "new_high": scale_new_high,
    "gap_size": scale_gap_size,
    "drift_confirm": scale_drift_confirm,
}


def score_criterion(scaling_type, max_pts, **measurements):
    """Score a single criterion. Returns 0-max_pts."""
    if scaling_type == "gate":
        return 0, False  # Gates handled separately
    fn = SCALING_FUNCTIONS.get(scaling_type)
    if not fn:
        return 0, False
    try:
        pts = fn(max_pts=max_pts, **measurements)
        return max(0, min(pts, max_pts)), pts > 0
    except Exception:
        return 0, False
