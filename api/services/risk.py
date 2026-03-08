"""
COD Risk Scorer — Phase 1

Scores an order from 0.0 (clean) to 1.0 (very likely fake).

Each signal has a weight based on how strongly it predicts a fake order.
Signals are grouped into 5 categories:
  1. Phone Number Quality
  2. Order History (same phone)
  3. Order Characteristics
  4. Address Quality
  5. Customer Name Quality

Final score = sum of triggered weights, capped at 1.0
Verdict:
  0.0 – 0.39  → low_risk    (proceed normally)
  0.40 – 0.69 → medium_risk (send WhatsApp, monitor)
  0.70 – 1.0  → high_risk   (send WhatsApp, flag for review)
"""

from datetime import datetime, timezone
from api.db.supabase import get_supabase


# ── Signal weights ─────────────────────────────────────────────────────────
# Each weight reflects how strongly that signal predicts a fake order.
# Based on real COD fraud patterns in PK/IN/ME markets.

WEIGHTS = {
    # ── Phone signals ──────────────────────────────────────────────
    "phone_missing":            0.50,   # No phone at all — can't confirm
    "phone_invalid_format":     0.40,   # Wrong length or non-numeric
    "phone_is_landline":        0.35,   # Landlines can't receive WhatsApp
    "phone_repeated_digits":    0.35,   # e.g. 03001111111, 03000000000
    "phone_sequential_digits":  0.30,   # e.g. 03001234567, 03009876543

    # ── Order history signals ──────────────────────────────────────
    "all_orders_cancelled":     0.55,   # 100% cancel rate (2+ orders)
    "high_cancel_rate":         0.40,   # 70%+ cancel rate (3+ orders)
    "recent_order_flood":       0.35,   # 3+ orders placed in last 24 hrs
    "repeat_auto_cancels":      0.40,   # 2+ auto-cancels (never replied)

    # ── Order characteristic signals ──────────────────────────────
    "very_high_quantity":       0.30,   # 10+ units on COD is suspicious
    "high_quantity":            0.15,   # 5–9 units, slightly elevated
    "zero_amount":              0.45,   # Order total is 0 — test order
    "very_high_cod_amount":     0.25,   # COD > 50,000 PKR is unusual
    "odd_hours_order":          0.15,   # Placed between 2am – 5am

    # ── Address signals ────────────────────────────────────────────
    "address_missing":          0.35,   # No address provided
    "address_too_short":        0.30,   # Under 10 characters
    "address_is_filler":        0.40,   # Contains "test", "asdf", "xyz" etc.
    "address_no_house_number":  0.15,   # No numeric component in address

    # ── Customer name signals ──────────────────────────────────────
    "name_missing":             0.20,   # No customer name
    "name_is_test":             0.40,   # "test", "asdf", "user", "abc" etc.
    "name_too_short":           0.25,   # Single character name
}

# ── Filler words that appear in fake addresses and names ──────────────────
FILLER_WORDS = {
    "test", "asdf", "qwerty", "xyz", "abc", "aaa", "bbb", "xxx",
    "user", "dummy", "fake", "sample", "na", "n/a", "none", "nil",
    "hello", "demo", "temp", "example",
}

# ── Valid Pakistani mobile prefixes (Jazz, Zong, Telenor, Ufone, SCO) ─────
PK_MOBILE_PREFIXES = {
    "0300", "0301", "0302", "0303", "0304", "0305", "0306", "0307",
    "0308", "0309",  # Jazz
    "0310", "0311", "0312", "0313", "0314", "0315", "0316", "0317",
    "0318", "0319",  # Zong
    "0320", "0321", "0322", "0323", "0324", "0325", "0326", "0327",
    "0328", "0329",  # Telenor
    "0330", "0331", "0332", "0333", "0334", "0335", "0336", "0337",
    "0338", "0339",  # Ufone
    "0340", "0341", "0342", "0343", "0344", "0345", "0346", "0347",
    "0348", "0349",  # Zong additional
    "0360", "0361", "0362", "0363",  # SCO
}


async def calculate_risk(order: dict) -> dict:
    """
    Evaluate all risk signals and return a score, flags, and verdict.
    """
    triggered: dict[str, float] = {}

    phone    = (order.get("phone") or "").strip()
    amount   = _safe_float(order.get("amount", 0))
    quantity = _safe_int(order.get("quantity", 1))
    address  = (order.get("address") or order.get("billing_address") or "").strip()
    name     = (order.get("customer") or "").strip()
    created  = order.get("created_at")  # ISO string if available

    # ── 1. Phone signals ───────────────────────────────────────────────────
    if not phone:
        triggered["phone_missing"] = WEIGHTS["phone_missing"]

    else:
        digits = "".join(c for c in phone if c.isdigit())

        # Format check — Pakistani numbers: 11 digits starting with valid prefix
        if not _is_valid_pk_mobile(digits):
            triggered["phone_invalid_format"] = WEIGHTS["phone_invalid_format"]

        # Landline check — Pakistani landlines: 021, 042, 051, 04x, 05x etc.
        elif digits[:3] in ("021", "042", "051", "041", "061", "071"):
            triggered["phone_is_landline"] = WEIGHTS["phone_is_landline"]

        # Repeated digits — e.g. 03001111111
        if _has_repeated_digits(digits, min_run=6):
            triggered["phone_repeated_digits"] = WEIGHTS["phone_repeated_digits"]

        # Sequential digits — e.g. 03001234567
        if _has_sequential_digits(digits, run_length=6):
            triggered["phone_sequential_digits"] = WEIGHTS["phone_sequential_digits"]

    # ── 2. Order history signals ───────────────────────────────────────────
    if phone:
        history_flags = await _check_order_history(phone, order.get("order_id", ""))
        triggered.update(history_flags)

    # ── 3. Order characteristic signals ───────────────────────────────────
    if amount == 0:
        triggered["zero_amount"] = WEIGHTS["zero_amount"]
    elif amount > 50_000:
        triggered["very_high_cod_amount"] = WEIGHTS["very_high_cod_amount"]

    if quantity >= 10:
        triggered["very_high_quantity"] = WEIGHTS["very_high_quantity"]
    elif quantity >= 5:
        triggered["high_quantity"] = WEIGHTS["high_quantity"]

    if created and _is_odd_hour(created):
        triggered["odd_hours_order"] = WEIGHTS["odd_hours_order"]

    # ── 4. Address signals ─────────────────────────────────────────────────
    if not address:
        triggered["address_missing"] = WEIGHTS["address_missing"]
    else:
        if len(address) < 10:
            triggered["address_too_short"] = WEIGHTS["address_too_short"]
        if _contains_filler(address):
            triggered["address_is_filler"] = WEIGHTS["address_is_filler"]
        if not any(c.isdigit() for c in address):
            triggered["address_no_house_number"] = WEIGHTS["address_no_house_number"]

    # ── 5. Customer name signals ───────────────────────────────────────────
    if not name:
        triggered["name_missing"] = WEIGHTS["name_missing"]
    else:
        if len(name) <= 1:
            triggered["name_too_short"] = WEIGHTS["name_too_short"]
        elif _contains_filler(name):
            triggered["name_is_test"] = WEIGHTS["name_is_test"]

    # ── Final score ────────────────────────────────────────────────────────
    score = round(min(sum(triggered.values()), 1.0), 2)

    verdict = (
        "high_risk"   if score >= 0.70 else
        "medium_risk" if score >= 0.40 else
        "low_risk"
    )

    return {
        "score":     score,
        "flags":     list(triggered.keys()),
        "breakdown": {k: round(v, 2) for k, v in triggered.items()},
        "verdict":   verdict,
    }


# ── Order history helpers ──────────────────────────────────────────────────

async def _check_order_history(phone: str, current_order_id: str) -> dict[str, float]:
    """Check past order behaviour for this phone number."""
    flags: dict[str, float] = {}
    supabase = get_supabase()

    past_orders = (
        supabase.table("orders")
        .select("status, created_at")
        .eq("phone", phone)
        .neq("order_id", current_order_id)
        .execute()
        .data
    )

    if not past_orders:
        return flags   # First order — no history to judge

    total       = len(past_orders)
    cancelled   = sum(1 for o in past_orders if o["status"] in ("cancelled", "auto_cancelled"))
    auto_cancel = sum(1 for o in past_orders if o["status"] == "auto_cancelled")
    cancel_rate = cancelled / total if total else 0

    # 100% cancel rate with at least 2 prior orders
    if total >= 2 and cancel_rate == 1.0:
        flags["all_orders_cancelled"] = WEIGHTS["all_orders_cancelled"]

    # 70%+ cancel rate with at least 3 prior orders
    elif total >= 3 and cancel_rate >= 0.7:
        flags["high_cancel_rate"] = WEIGHTS["high_cancel_rate"]

    # 2+ auto-cancels means they never responded to any WhatsApp at all
    if auto_cancel >= 2:
        flags["repeat_auto_cancels"] = WEIGHTS["repeat_auto_cancels"]

    # 3+ orders placed by same phone in last 24 hours
    if _orders_in_last_n_hours(past_orders, hours=24) >= 3:
        flags["recent_order_flood"] = WEIGHTS["recent_order_flood"]

    return flags


# ── Phone validation helpers ───────────────────────────────────────────────

def _is_valid_pk_mobile(digits: str) -> bool:
    """Accepts both 03XXXXXXXXX (11 digits) and 923XXXXXXXXX (12 digits)."""
    if len(digits) == 11 and digits[:4] in PK_MOBILE_PREFIXES:
        return True
    if len(digits) == 12 and digits.startswith("92"):
        local = "0" + digits[2:]  # convert 923001234567 -> 03001234567
        return local[:4] in PK_MOBILE_PREFIXES
    return False


def _has_repeated_digits(digits: str, min_run: int = 6) -> bool:
    """True if any single digit repeats consecutively for min_run times."""
    count = 1
    for i in range(1, len(digits)):
        count = count + 1 if digits[i] == digits[i - 1] else 1
        if count >= min_run:
            return True
    return False


def _has_sequential_digits(digits: str, run_length: int = 6) -> bool:
    """True if there's a strictly ascending or descending run of run_length digits."""
    asc = des = 1
    for i in range(1, len(digits)):
        diff = int(digits[i]) - int(digits[i - 1])
        asc  = asc + 1 if diff ==  1 else 1
        des  = des + 1 if diff == -1 else 1
        if asc >= run_length or des >= run_length:
            return True
    return False


# ── Address / name helpers ─────────────────────────────────────────────────

def _contains_filler(text: str) -> bool:
    """True if text contains any known filler/test word."""
    words = set(text.lower().split())
    return bool(words & FILLER_WORDS)


# ── Date / time helpers ────────────────────────────────────────────────────

def _is_odd_hour(created_at_iso: str) -> bool:
    """True if the order was placed between 2am and 5am UTC."""
    try:
        dt = datetime.fromisoformat(created_at_iso.replace("Z", "+00:00"))
        return 2 <= dt.hour < 5
    except Exception:
        return False


def _orders_in_last_n_hours(orders: list[dict], hours: int) -> int:
    """Count how many orders from the list were placed within the last N hours."""
    now   = datetime.now(timezone.utc)
    count = 0
    for o in orders:
        try:
            created = datetime.fromisoformat(o["created_at"].replace("Z", "+00:00"))
            if (now - created).total_seconds() / 3600 <= hours:
                count += 1
        except Exception:
            pass
    return count


# ── Type safety helpers ────────────────────────────────────────────────────

def _safe_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 1
