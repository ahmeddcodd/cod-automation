"""
COD Risk Scorer

Scores an order from 0.0 (clean) to 1.0 (very likely fake).
Result + full context is passed to the LLM decision engine.

Signal categories:
  1. Phone Number Quality
  2. Order History (same phone)
  3. Order Characteristics
  4. Address Quality
  5. Customer Name Quality

Verdict thresholds:
  0.00 – 0.39  →  low_risk     proceed normally
  0.40 – 0.69  →  medium_risk  flag for review
  0.70 – 1.00  →  high_risk    strong fraud signal
"""

from datetime import datetime, timezone
from api.db.supabase import get_supabase


# ── Signal weights ─────────────────────────────────────────────────────────
# Each weight = how strongly this signal predicts a fake COD order.
# Calibrated for PK market. Sum is capped at 1.0.

WEIGHTS: dict[str, float] = {
    # Phone
    "phone_missing":            0.50,  # Can't confirm without a phone
    "phone_invalid_format":     0.40,  # Not a valid PK mobile
    "phone_is_landline":        0.35,  # Landlines can't receive WhatsApp
    "phone_repeated_digits":    0.35,  # e.g. 0300-1111111
    "phone_sequential_digits":  0.30,  # e.g. 0300-1234567

    # Order history
    "all_orders_cancelled":     0.55,  # 100% cancel rate (2+ orders)
    "high_cancel_rate":         0.40,  # 70%+ cancel rate (3+ orders)
    "repeat_auto_cancels":      0.40,  # Never replied to WhatsApp (2+ times)
    "recent_order_flood":       0.35,  # 3+ orders from same phone in 24 hrs

    # Order characteristics
    "zero_amount":              0.45,  # PKR 0 — almost always a test
    "very_high_quantity":       0.30,  # 10+ units COD is unusual
    "very_high_cod_amount":     0.25,  # Over PKR 50,000 COD
    "high_quantity":            0.15,  # 5–9 units, slightly elevated
    "odd_hours_order":          0.15,  # Placed 2am–5am Pakistan time

    # Address
    "address_missing":          0.35,  # No address at all
    "address_is_filler":        0.40,  # Contains test/fake words
    "address_too_short":        0.30,  # Under 10 characters
    "address_no_house_number":  0.15,  # No numeric component

    # Name
    "name_is_test":             0.40,  # Contains test/fake words
    "name_too_short":           0.25,  # Single character
    "name_missing":             0.20,  # No name provided
}

# Human-readable descriptions sent to the LLM alongside flag names.
# This makes LLM decisions significantly more accurate.
SIGNAL_DESCRIPTIONS: dict[str, str] = {
    "phone_missing":            "No phone number provided — cannot send WhatsApp confirmation",
    "phone_invalid_format":     "Phone number is not a valid Pakistani mobile number",
    "phone_is_landline":        "Phone appears to be a landline — cannot receive WhatsApp",
    "phone_repeated_digits":    "Phone number has 6+ repeated digits (e.g. 0300-1111111) — likely fake",
    "phone_sequential_digits":  "Phone number has 6+ sequential digits (e.g. 0300-1234567) — likely fake",
    "all_orders_cancelled":     "Every previous order from this phone was cancelled or ignored",
    "high_cancel_rate":         "70%+ of previous orders from this phone were cancelled",
    "repeat_auto_cancels":      "Customer never replied to WhatsApp on 2+ previous orders",
    "recent_order_flood":       "3+ orders placed from same phone in the last 24 hours",
    "zero_amount":              "Order total is PKR 0 — almost certainly a test or bot order",
    "very_high_quantity":       "10+ units on a single COD order is highly unusual",
    "very_high_cod_amount":     "COD amount exceeds PKR 50,000 — unusually high for cash payment",
    "high_quantity":            "5–9 units on COD — slightly elevated risk",
    "odd_hours_order":          "Order placed between 2am–5am Pakistan time",
    "address_missing":          "No delivery address provided",
    "address_is_filler":        "Address contains test/placeholder words (e.g. 'test', 'asdf', 'xyz')",
    "address_too_short":        "Address is under 10 characters — not a real delivery address",
    "address_no_house_number":  "Address has no house/plot number",
    "name_is_test":             "Customer name contains test/fake words",
    "name_too_short":           "Customer name is a single character",
    "name_missing":             "No customer name provided",
}

# Words that strongly indicate fake/test entries in names and addresses.
# Deliberately conservative — only clear fake indicators, not ambiguous words.
FILLER_WORDS: frozenset[str] = frozenset({
    "test", "testing", "asdf", "qwerty", "xyz", "xxx",
    "aaa", "bbb", "ccc", "dummy", "fake", "sample",
    "none", "nil", "n/a", "null", "demo", "temp", "example",
})

# Valid Pakistani mobile carrier prefixes (Jazz, Zong, Telenor, Ufone, SCO)
PK_MOBILE_PREFIXES: frozenset[str] = frozenset({
    # Jazz / Warid
    "0300", "0301", "0302", "0303", "0304", "0305", "0306", "0307", "0308", "0309",
    # Zong
    "0310", "0311", "0312", "0313", "0314", "0315", "0316", "0317", "0318", "0319",
    "0340", "0341", "0342", "0343", "0344", "0345", "0346", "0347", "0348", "0349",
    # Telenor
    "0320", "0321", "0322", "0323", "0324", "0325", "0326", "0327", "0328", "0329",
    # Ufone
    "0330", "0331", "0332", "0333", "0334", "0335", "0336", "0337", "0338", "0339",
    # SCO
    "0360", "0361", "0362", "0363",
})

# Pakistani landline area codes in local (03XX) normalised format
PK_LANDLINE_PREFIXES: frozenset[str] = frozenset({
    "021",  # Karachi
    "041",  # Faisalabad
    "042",  # Lahore
    "051",  # Islamabad / Rawalpindi
    "055",  # Gujranwala
    "061",  # Multan
    "071",  # Sukkur
    "081",  # Quetta
    "091",  # Peshawar
})

# Thresholds — all PKR / PK market specific
HIGH_COD_AMOUNT_PKR = 50_000   # above this is unusual for COD
HIGH_QUANTITY       = 10       # 10+ units — suspicious
MEDIUM_QUANTITY     = 5        # 5–9 units — slightly elevated

# Odd hours in UTC that correspond to 2am–5am Pakistan Standard Time (UTC+5)
# 2am PKT = 21:00 UTC,  5am PKT = 00:00 UTC
# So: UTC hours 21, 22, 23, 0 are all "odd hours" in Pakistan
ODD_HOURS_UTC: frozenset[int] = frozenset({21, 22, 23, 0})


# ── Main entry point ───────────────────────────────────────────────────────

async def calculate_risk(order: dict) -> dict:
    """
    Evaluate all risk signals for a COD order.

    Returns:
      score            float 0.0–1.0
      verdict          low_risk / medium_risk / high_risk
      flags            list of triggered signal names
      breakdown        {signal: weight} for triggered signals
      signal_context   {signal: human-readable description} — fed to LLM
      past_order_count int — total past orders from this phone
      confirmed_count  int — how many were confirmed
      cancelled_count  int — how many were cancelled / auto-cancelled
      address_used     str — flattened address that was evaluated
    """
    triggered: dict[str, float] = {}

    phone    = (order.get("phone") or "").strip()
    amount   = _safe_float(order.get("amount", 0))
    quantity = _safe_int(order.get("quantity", 1))
    name     = (order.get("customer") or "").strip()
    created  = order.get("created_at")
    address  = _extract_address(order)

    # ── 1. Phone ───────────────────────────────────────────────────────────
    if not phone:
        triggered["phone_missing"] = WEIGHTS["phone_missing"]
    else:
        digits    = "".join(c for c in phone if c.isdigit())
        local_fmt = _to_local_format(digits)

        if not _is_valid_pk_mobile(local_fmt):
            triggered["phone_invalid_format"] = WEIGHTS["phone_invalid_format"]

        # Landline check is independent — a number can fail mobile validation
        # AND be a landline (e.g. 021-XXXXXXX)
        if local_fmt[:3] in PK_LANDLINE_PREFIXES:
            triggered["phone_is_landline"] = WEIGHTS["phone_is_landline"]

        if _has_repeated_digits(digits, min_run=6):
            triggered["phone_repeated_digits"] = WEIGHTS["phone_repeated_digits"]

        if _has_sequential_digits(digits, run_length=6):
            triggered["phone_sequential_digits"] = WEIGHTS["phone_sequential_digits"]

    # ── 2. Order history ───────────────────────────────────────────────────
    past_order_count = 0
    confirmed_count  = 0
    cancelled_count  = 0

    if phone:
        history_flags, past_order_count, confirmed_count, cancelled_count = (
            await _check_order_history(phone, str(order.get("order_id", "")))
        )
        triggered.update(history_flags)

    # ── 3. Order characteristics ───────────────────────────────────────────
    if amount == 0:
        triggered["zero_amount"] = WEIGHTS["zero_amount"]
    elif amount > HIGH_COD_AMOUNT_PKR:
        triggered["very_high_cod_amount"] = WEIGHTS["very_high_cod_amount"]

    if quantity >= HIGH_QUANTITY:
        triggered["very_high_quantity"] = WEIGHTS["very_high_quantity"]
    elif quantity >= MEDIUM_QUANTITY:
        triggered["high_quantity"] = WEIGHTS["high_quantity"]

    if created and _is_odd_hour_pkt(created):
        triggered["odd_hours_order"] = WEIGHTS["odd_hours_order"]

    # ── 4. Address ─────────────────────────────────────────────────────────
    if not address:
        triggered["address_missing"] = WEIGHTS["address_missing"]
    else:
        if len(address) < 10:
            triggered["address_too_short"] = WEIGHTS["address_too_short"]
        if _contains_filler(address):
            triggered["address_is_filler"] = WEIGHTS["address_is_filler"]
        if not any(c.isdigit() for c in address):
            triggered["address_no_house_number"] = WEIGHTS["address_no_house_number"]

    # ── 5. Name ────────────────────────────────────────────────────────────
    if not name:
        triggered["name_missing"] = WEIGHTS["name_missing"]
    else:
        if len(name) <= 1:
            triggered["name_too_short"] = WEIGHTS["name_too_short"]
        elif _contains_filler(name):
            triggered["name_is_test"] = WEIGHTS["name_is_test"]

    # ── Score + verdict ────────────────────────────────────────────────────
    score = round(min(sum(triggered.values()), 1.0), 2)

    verdict = (
        "high_risk"   if score >= 0.70 else
        "medium_risk" if score >= 0.40 else
        "low_risk"
    )

    # ── Build signal context for LLM ──────────────────────────────────────
    # Each triggered flag gets a plain-English description so the LLM
    # understands exactly what was detected, not just a flag name.
    signal_context = {
        flag: SIGNAL_DESCRIPTIONS[flag]
        for flag in triggered
        if flag in SIGNAL_DESCRIPTIONS
    }

    return {
        "score":            score,
        "verdict":          verdict,
        "flags":            list(triggered.keys()),
        "breakdown":        {k: round(v, 2) for k, v in triggered.items()},
        "signal_context":   signal_context,
        "past_order_count": past_order_count,
        "confirmed_count":  confirmed_count,
        "cancelled_count":  cancelled_count,
        "address_used":     address,
    }


# ── Address extraction ─────────────────────────────────────────────────────

def _extract_address(order: dict) -> str:
    """
    Returns a flat address string from either a plain string field
    or a Shopify billing/shipping address dict.
    Tries billing_address before shipping_address.
    """
    # Plain string fields (curl tests + some integrations)
    for key in ("address", "address1"):
        val = order.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    # Shopify dict format
    for key in ("billing_address", "shipping_address"):
        addr = order.get(key)
        if isinstance(addr, dict):
            parts = [
                addr.get("address1") or "",
                addr.get("address2") or "",
                addr.get("city")     or "",
                addr.get("province") or "",
            ]
            flat = " ".join(str(p).strip() for p in parts if str(p).strip())
            if flat.strip():
                return flat.strip()

    return ""


# ── Order history ──────────────────────────────────────────────────────────

async def _check_order_history(
    phone: str, current_order_id: str
) -> tuple[dict[str, float], int, int, int]:
    """
    Returns (flags, total, confirmed, cancelled) for this phone number.
    confirmed_count lets the LLM calculate confirmation rate, not just cancel rate.
    """
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
        return flags, 0, 0, 0

    total     = len(past_orders)
    confirmed = sum(1 for o in past_orders if o["status"] == "confirmed")
    cancelled = sum(
        1 for o in past_orders
        if o["status"] in ("cancelled", "auto_cancelled", "auto_rejected")
    )
    auto_cancelled = sum(
        1 for o in past_orders
        if o["status"] == "auto_cancelled"
    )
    cancel_rate = cancelled / total if total else 0.0

    # 100% cancel rate with at least 2 prior orders
    if total >= 2 and cancel_rate == 1.0:
        flags["all_orders_cancelled"] = WEIGHTS["all_orders_cancelled"]

    # 70%+ cancel rate with at least 3 prior orders
    elif total >= 3 and cancel_rate >= 0.7:
        flags["high_cancel_rate"] = WEIGHTS["high_cancel_rate"]

    # Never replied to WhatsApp on 2+ previous orders
    if auto_cancelled >= 2:
        flags["repeat_auto_cancels"] = WEIGHTS["repeat_auto_cancels"]

    # 3+ orders placed from this phone in the last 24 hours
    if _count_orders_in_last_hours(past_orders, hours=24) >= 3:
        flags["recent_order_flood"] = WEIGHTS["recent_order_flood"]

    return flags, total, confirmed, cancelled


# ── Phone helpers ──────────────────────────────────────────────────────────

def _to_local_format(digits: str) -> str:
    """Normalise 923001234567 → 03001234567 for prefix matching."""
    if len(digits) == 12 and digits.startswith("92"):
        return "0" + digits[2:]
    return digits


def _is_valid_pk_mobile(local_digits: str) -> bool:
    """
    Expects number already in local format (03XXXXXXXXX, 11 digits).
    Returns True if it starts with a known carrier prefix.
    """
    return len(local_digits) == 11 and local_digits[:4] in PK_MOBILE_PREFIXES


def _has_repeated_digits(digits: str, min_run: int = 6) -> bool:
    """True if any digit repeats consecutively for min_run or more."""
    count = 1
    for i in range(1, len(digits)):
        count = count + 1 if digits[i] == digits[i - 1] else 1
        if count >= min_run:
            return True
    return False


def _has_sequential_digits(digits: str, run_length: int = 6) -> bool:
    """True if there is a strictly ascending or descending run of run_length."""
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
    """
    True if any word-token in text exactly matches a known filler word.
    Uses word-boundary splitting so 'testing123' does NOT match 'test'.
    """
    tokens = set(text.lower().split())
    return bool(tokens & FILLER_WORDS)


# ── Time helpers ───────────────────────────────────────────────────────────

def _is_odd_hour_pkt(created_at_iso: str) -> bool:
    """
    True if the order was placed between 2am and 5am Pakistan Standard Time.
    PKT = UTC+5, so:
      2am PKT = 21:00 UTC
      3am PKT = 22:00 UTC
      4am PKT = 23:00 UTC
      5am PKT = 00:00 UTC
    ODD_HOURS_UTC covers all four of these hours exactly.
    """
    try:
        dt = datetime.fromisoformat(created_at_iso.replace("Z", "+00:00"))
        utc_hour = dt.astimezone(timezone.utc).hour
        return utc_hour in ODD_HOURS_UTC
    except Exception:
        return False


def _count_orders_in_last_hours(orders: list[dict], hours: int) -> int:
    now = datetime.now(timezone.utc)
    count = 0
    for o in orders:
        try:
            created = datetime.fromisoformat(
                o["created_at"].replace("Z", "+00:00")
            )
            if (now - created).total_seconds() / 3600 <= hours:
                count += 1
        except Exception:
            pass
    return count


# ── Type safety ────────────────────────────────────────────────────────────

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