"""
LLM Order Decision Engine

Takes the full risk result from calculate_risk() and makes a final
actionable decision on what to do with the order.

Decisions:
  proceed         → send WhatsApp confirmation normally
  flag_for_review → send WhatsApp + notify merchant (suspicious but uncertain)
  auto_reject     → cancel immediately, no WhatsApp (clearly fake)

The rule-based fallback is intentionally conservative:
only auto_rejects when there is overwhelming evidence.
The LLM handles the nuanced middle ground.
"""

import json
import os

import httpx

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL   = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """
You are a fraud detection assistant for a Pakistani e-commerce COD (Cash on Delivery) platform.

You will receive a JSON object containing:
- risk_score: float 0.0–1.0
- risk_verdict: low_risk / medium_risk / high_risk
- signal_context: {flag_name: plain English description of what was detected}
- order: {customer, phone, product, amount, currency, quantity, address}
- history: {past_orders, confirmed, cancelled, is_first_order, cancellation_rate}

Your job is to output exactly one of three decisions:

PROCEED
  Send WhatsApp confirmation to the customer normally.
  Use when:
  - Risk is low and no strong fraud signals
  - Only minor signals triggered (e.g. address_no_house_number alone)
  - First order from a customer with a normal order profile
  - Medium risk but signals are weak or explainable

FLAG_FOR_REVIEW
  Send WhatsApp but also alert the merchant to watch this order.
  Use when:
  - Medium-to-high risk with 2–3 suspicious signals
  - High risk but order details look plausible (normal amount, real name)
  - Repeat customer with mixed history (some confirmed, some cancelled)
  - High order amount that needs extra attention

AUTO_REJECT
  Cancel the order immediately. Do not send WhatsApp.
  Use when ALL of the following are true:
  - Multiple strong signals stack together (not just one)
  - History shows clear fraudulent pattern (100% cancel rate across 3+ orders)
  - OR: test name + test address + zero amount all together
  - OR: repeat_auto_cancels AND all_orders_cancelled AND phone issues together
  NEVER auto_reject based on a single signal alone.
  NEVER auto_reject a first-time customer regardless of score.

Respond ONLY with valid JSON — no extra text:
{"decision": "proceed", "reason": "brief explanation"}
"""


async def make_order_decision(order_data: dict, risk: dict) -> dict:
    """
    Makes a final decision on what to do with an incoming COD order.

    Args:
      order_data: the normalised order dict from webhooks.py
      risk:       the full result from calculate_risk()

    Returns:
      {
        "decision":  "proceed" | "flag_for_review" | "auto_reject",
        "reason":    str,
        "source":    "llm" | "rules"   (so you know which made the call)
      }
    """
    api_key = os.getenv("GROQ_API_KEY")

    past      = risk.get("past_order_count", 0)
    confirmed = risk.get("confirmed_count", 0)
    cancelled = risk.get("cancelled_count", 0)
    cancel_rate = f"{int(cancelled / past * 100)}%" if past else "N/A (first order)"

    payload = {
        "risk_score":    risk["score"],
        "risk_verdict":  risk["verdict"],
        "signal_context": risk.get("signal_context", {}),
        "order": {
            "customer": order_data.get("customer", ""),
            "phone":    order_data.get("phone", ""),
            "product":  order_data.get("product", ""),
            "amount":   f"{order_data.get('currency', 'PKR')} {order_data.get('amount', '0')}",
            "quantity": order_data.get("quantity", 1),
            "address":  risk.get("address_used", ""),
        },
        "history": {
            "past_orders":       past,
            "confirmed":         confirmed,
            "cancelled":         cancelled,
            "is_first_order":    past == 0,
            "cancellation_rate": cancel_rate,
        },
    }

    # Rule-based fallback decision — used if LLM is unavailable
    fallback = _rule_based_decision(risk)

    if not api_key:
        print("GROQ_API_KEY missing — using rule-based order decision")
        return {**fallback, "source": "rules"}

    try:
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
                    "max_tokens":  80,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
                timeout=8,
            )

        data    = response.json()
        content = data["choices"][0]["message"]["content"].strip()
        parsed  = json.loads(content)

        decision = str(parsed.get("decision", "")).strip().lower()
        reason   = str(parsed.get("reason", "")).strip()

        if decision not in ("proceed", "flag_for_review", "auto_reject"):
            print(f"LLM returned unknown decision '{decision}' — using rule-based fallback")
            return {**fallback, "source": "rules"}

        # Safety override: never auto_reject a first-time customer
        if decision == "auto_reject" and past == 0:
            decision = "flag_for_review"
            reason   = f"Downgraded from auto_reject: first-time customer. Original: {reason}"

        print(f"Order decision: {decision} | {reason}")
        return {"decision": decision, "reason": reason, "source": "llm"}

    except Exception as e:
        print(f"Order decision LLM failed: {e} — using rule-based fallback")
        return {**fallback, "source": "rules"}


def _rule_based_decision(risk: dict) -> dict:
    """
    Conservative rule-based fallback.
    Only auto_rejects when evidence is overwhelming.
    """
    score  = risk["score"]
    flags  = set(risk.get("flags", []))

    # Clear repeat-fraudster pattern
    if (
        "all_orders_cancelled" in flags
        and "repeat_auto_cancels" in flags
        and score >= 0.80
    ):
        return {
            "decision": "auto_reject",
            "reason":   "Repeat fraudster: 100% cancel rate + never replied to WhatsApp",
        }

    # Obvious test order
    if "zero_amount" in flags and ("name_is_test" in flags or "address_is_filler" in flags):
        return {
            "decision": "auto_reject",
            "reason":   "Test order: zero amount with fake name or address",
        }

    if score >= 0.55:
        return {
            "decision": "flag_for_review",
            "reason":   f"Risk score {score} with flags: {', '.join(flags)}",
        }

    return {
        "decision": "proceed",
        "reason":   f"Risk score {score} — within acceptable range",
    }
