"""
LLM-based WhatsApp reply parser — Phase 2

Uses Groq + Llama 3.3 to understand customer replies in any language.
Risk context (score, verdict, signal descriptions, history) is passed
to the LLM so it can factor in fraud signals when intent is ambiguous.

Returns: confirmed / cancelled / unclear
"""

import json
import os
import re

import httpx

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL   = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """
You are an intent classifier for a WhatsApp order confirmation system in Pakistan.

A customer placed a Cash on Delivery order and was asked to confirm or cancel it.
You will receive:
  - customer_reply: what the customer typed
  - order_context: order details + risk assessment

Classify the customer's PRIMARY INTENT as one of:
  confirmed  → customer wants the order
  cancelled  → customer does not want the order
  unclear    → cannot determine intent

Language guide:
  The customer may write in English, Roman Urdu, Urdu script, or mixed.
  Positive signals: yes, ok, sure, haan, han, ji, theek hai, bilkul, bhej do, send, zaroor
  Negative signals: no, cancel, nahi, nahin, nai, nhi, mat, band, rehne do, wapas, nahi chahiye

Conversational reply guide:
  "yeah sure send it bro"       → confirmed
  "haan bhai bhej do"           → confirmed
  "kal bhej dena"               → confirmed (send tomorrow = they want it)
  "haan bhej do lekin jaldi"    → confirmed (conditional but positive)
  "okay okay confirm hai"       → confirmed
  "nahi yar abhi paisa nahi"    → cancelled
  "abhi nahi chahiye"           → cancelled
  "cancel kar do bhai"          → cancelled
  "kitna time lagega?"          → unclear (question, not a decision)
  "assalam o alaikum"           → unclear (greeting only)

Risk context usage:
  Use risk signals ONLY when the reply is genuinely ambiguous (unclear).
  High risk + ambiguous reply → lean toward unclear (ask again).
  Low/medium risk + ambiguous reply → lean toward confirmed.
  Never override a clear positive or negative reply based on risk.

Respond ONLY with valid JSON:
{"intent": "confirmed", "reason": "customer said bhej do"}
"""


# ── Regex fast-path — catches unambiguous replies before calling LLM ──────
# Only used when the reply is so clear that LLM is unnecessary.

_CLEAR_POSITIVE = re.compile(
    r"\b(yes|confirm(?:ed)?|ok|okay|sure|haan|han|ji|bilkul|bhej\s?do|send\s?it|send\s?karo)\b",
    re.IGNORECASE,
)
_CLEAR_NEGATIVE = re.compile(
    r"\b(no|cancel(?:led)?|nahi|nahin|nai|nhi|mat\s?bhejo|nahi\s?chahiye|rehne\s?do)\b",
    re.IGNORECASE,
)


async def parse_reply_with_llm(customer_reply: str, order: dict | None = None) -> str:
    """
    Parse a customer WhatsApp reply.

    Args:
      customer_reply: raw text from the customer
      order:          the full order dict (includes risk_score, risk_flags,
                      risk_verdict, signal_context if available)

    Returns: "confirmed" | "cancelled" | "unclear"
    """
    # Fast path: unambiguous reply (no need to burn LLM tokens)
    fast = _fast_path(customer_reply)
    if fast:
        print(f"Fast-path intent for '{customer_reply}': {fast}")
        return fast

    context = _build_context(order or {})
    api_key = os.getenv("GROQ_API_KEY")

    if not api_key:
        print("GROQ_API_KEY missing — using keyword fallback")
        return _keyword_fallback(customer_reply, context)

    try:
        payload = {
            "customer_reply": customer_reply,
            "order_context":  context,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                GROQ_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":    GROQ_MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                    "max_tokens":  60,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
                timeout=8,
            )

        data    = response.json()
        content = data["choices"][0]["message"]["content"].strip()
        parsed  = json.loads(content)

        intent = str(parsed.get("intent", "unclear")).strip().lower()
        reason = str(parsed.get("reason", "")).strip()
        print(f"LLM intent for '{customer_reply}': {intent} | {reason}")

        if intent not in ("confirmed", "cancelled", "unclear"):
            return _keyword_fallback(customer_reply, context)

        return intent

    except Exception as e:
        print(f"Groq LLM failed: {e} — using keyword fallback")
        return _keyword_fallback(customer_reply, context)


def _fast_path(text: str) -> str | None:
    """
    Returns a definitive intent for clear-cut replies without calling LLM.
    Only fires when there is a clear signal with NO conflicting signal.
    """
    has_positive = bool(_CLEAR_POSITIVE.search(text))
    has_negative = bool(_CLEAR_NEGATIVE.search(text))

    if has_positive and not has_negative:
        return "confirmed"
    if has_negative and not has_positive:
        return "cancelled"
    return None  # ambiguous — let LLM decide


def _build_context(order: dict) -> dict:
    """
    Build the context object sent to the LLM alongside the customer reply.
    Includes risk score, verdict, human-readable signal descriptions,
    and order history counts so the LLM has the full picture.
    """
    risk_score = _safe_float(order.get("risk_score"), default=0.0)

    risk_verdict = str(order.get("risk_verdict") or "").strip().lower()
    if risk_verdict not in {"low_risk", "medium_risk", "high_risk"}:
        risk_verdict = (
            "high_risk"   if risk_score >= 0.70 else
            "medium_risk" if risk_score >= 0.40 else
            "low_risk"
        )

    risk_flags_raw = order.get("risk_flags", [])
    risk_flags = (
        [str(f).strip() for f in risk_flags_raw if str(f).strip()]
        if isinstance(risk_flags_raw, list) else []
    )

    # signal_context contains human-readable descriptions from risk.py
    signal_context = order.get("signal_context", {})
    if not isinstance(signal_context, dict):
        signal_context = {}

    return {
        "order_id":       str(order.get("order_id") or "").strip(),
        "customer":       str(order.get("customer") or "").strip(),
        "product":        str(order.get("product") or "").strip(),
        "amount":         f"{order.get('currency', 'PKR')} {order.get('amount', '0')}",
        "risk_score":     risk_score,
        "risk_verdict":   risk_verdict,
        "risk_flags":     risk_flags,
        "signal_context": signal_context,   # ← human-readable descriptions
    }


def _keyword_fallback(text: str, context: dict) -> str:
    """
    Minimal keyword fallback when Groq is unavailable.
    Uses risk verdict to break ties on ambiguous replies.
    """
    cleaned = text.lower().strip()

    positive = any(w in cleaned for w in ["yes", "confirm", "ok", "haan", "han", "ji", "bhej", "send", "bilkul"])
    negative = any(w in cleaned for w in ["no", "cancel", "nahi", "nahin", "nai", "nhi", "mat"])

    if positive and not negative:
        return "confirmed"
    if negative and not positive:
        return "cancelled"

    # Truly ambiguous — use risk to break tie
    if context.get("risk_verdict") == "high_risk":
        return "unclear"
    return "unclear"


def _safe_float(value, default: float = 0.0) -> float:
    try:
        result = float(value)
        return max(0.0, min(1.0, result))
    except (TypeError, ValueError):
        return default