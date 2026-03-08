import os
from fastapi import APIRouter, Request
from api.db.supabase import get_supabase
from api.services.whatsapp import send_message
from api.services.shopify import confirm_order, cancel_order
from api.services.llm import parse_reply_with_llm

router = APIRouter()


def _normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    return digits


def _phone_variants(value: str) -> list[str]:
    digits = _normalize_phone(value)
    variants: list[str] = []

    def _add(candidate: str) -> None:
        candidate = str(candidate or "").strip()
        if candidate and candidate not in variants:
            variants.append(candidate)

    _add(value)
    _add(digits)
    if len(digits) == 11 and digits.startswith("03"):
        _add("92" + digits[1:])
    if len(digits) == 12 and digits.startswith("92"):
        _add("0" + digits[2:])

    return variants


@router.get("/reply")
async def verify_webhook(request: Request):
    """Meta calls this GET to verify the webhook URL."""
    params    = request.query_params
    mode      = params.get("hub.mode")
    token     = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == os.getenv("META_VERIFY_TOKEN"):
        return int(challenge)
    return {"error": "verification failed"}


@router.post("/reply")
async def handle_reply(request: Request):
    """
    Meta sends all incoming WhatsApp messages here.
    Phase 2: Uses LLM to understand fuzzy replies in English + Urdu.
    """
    data = await request.json()

    if "object" not in data:
        return {"status": "ignored"}

    try:
        message = data["entry"][0]["changes"][0]["value"]["messages"][0]
        phone   = message["from"]

        # Handle both text replies and quick reply button taps
        if message.get("type") == "button":
            text = message.get("button", {}).get("text", "").strip()
        else:
            text = message.get("text", {}).get("body", "").strip()

    except (KeyError, IndexError):
        return {"status": "could not parse message"}

    if not text:
        return {"status": "empty message"}

    print(f"WhatsApp reply from {phone}: '{text}'")

    # Find most recent pending order for this phone
    supabase = get_supabase()
    pending_order = None
    for candidate_phone in _phone_variants(phone):
        result = (
            supabase.table("orders")
            .select("*")
            .eq("phone", candidate_phone)
            .eq("status", "pending")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            pending_order = result.data[0]
            break

    if not pending_order:
        return {"status": "no pending order found"}

    order = pending_order
    order_id = order["order_id"]

    # Phase 2 — LLM parses the reply using order + risk context.
    reply = await parse_reply_with_llm(text, order)
    print(f"LLM decision for order {order_id}: {reply}")

    if reply == "confirmed":
        await confirm_order(order_id, order["merchant_id"])
        supabase.table("orders").update({
            "status": "confirmed",
            "reply":  text,
        }).eq("order_id", order_id).execute()

        await send_message(
            phone,
            f"✅ Your order *{order['product']}* has been confirmed!\n"
            f"Thank you, {order['customer']}. We'll deliver it soon. 🚚"
        )

    elif reply == "cancelled":
        await cancel_order(order_id, order["merchant_id"])
        supabase.table("orders").update({
            "status": "cancelled",
            "reply":  text,
        }).eq("order_id", order_id).execute()

        await send_message(
            phone,
            f"❌ Your order *{order['product']}* has been cancelled.\n"
            f"No worries, {order['customer']}. Feel free to order again anytime! 🛍️"
        )

    else:
        # Unclear — respond in English + Urdu
        supabase.table("orders").update({"reply": text}).eq("order_id", order_id).execute()

        await send_message(
            phone,
            f"Hum aapka jawab samajh nahi sakay. 🙏\n"
            f"We could not understand your reply.\n\n"
            f"Please reply with:\n"
            f"✅ *YES* — Order confirm karna hai\n"
            f"❌ *NO* — Order cancel karna hai"
        )

    return {"status": "handled", "reply": reply}
