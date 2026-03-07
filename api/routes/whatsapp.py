import os
from fastapi import APIRouter, Request
from api.db.supabase import get_supabase
from api.services.whatsapp import send_message
from api.services.shopify import confirm_order, cancel_order

router = APIRouter()

# Keywords for confirmation — English + Urdu
CONFIRM_KEYWORDS = [
    "yes", "confirm", "ok", "okay", "sure", "yep", "yup",
    "haan", "ha", "ji", "han", "theek", "bilkul", "zaroor", "1",
]

# Keywords for cancellation — English + Urdu
CANCEL_KEYWORDS = [
    "no", "cancel", "nope", "nah", "stop",
    "nahi", "na", "nai", "band", "mat", "nhi", "2",
]


def parse_reply(text: str) -> str | None:
    """Map customer's reply to confirmed / cancelled / None."""
    cleaned = text.lower().strip()
    if any(kw in cleaned for kw in CONFIRM_KEYWORDS):
        return "confirmed"
    if any(kw in cleaned for kw in CANCEL_KEYWORDS):
        return "cancelled"
    return None


@router.get("/reply")
async def verify_webhook(request: Request):
    params = request.query_params

    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == os.getenv("META_VERIFY_TOKEN"):
        return int(challenge)

    return {"error": "verification failed"}


@router.post("/reply")
async def handle_reply(request: Request):
    """
    Meta Cloud API sends all incoming WhatsApp messages here.
    We parse the reply and immediately act on it.
    """
    data = await request.json()

    if "object" not in data:
        return {"status": "ignored"}

    # Extract message from Meta's payload
    try:
        message = data["entry"][0]["changes"][0]["value"]["messages"][0]
        phone   = message["from"]
        text    = message.get("text", {}).get("body", "").strip()
    except (KeyError, IndexError):
        return {"status": "could not parse message"}

    if not text:
        return {"status": "empty message"}

    # Find the most recent pending order for this phone
    supabase = get_supabase()
    result = (
        supabase.table("orders")
        .select("*")
        .eq("phone", phone)
        .eq("status", "pending")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )

    if not result.data:
        return {"status": "no pending order found for this number"}

    order    = result.data[0]
    order_id = order["order_id"]
    reply    = parse_reply(text)

    if reply == "confirmed":
        # Confirm on Shopify + update DB
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
        # Cancel on Shopify + update DB
        await cancel_order(order_id, order["merchant_id"])
        supabase.table("orders").update({
            "status": "cancelled",
            "reply":  text,
        }).eq("order_id", order_id).execute()

        await send_message(
            phone,
            f"❌ Your order *{order['product']}* has been cancelled as requested.\n"
            f"No worries, {order['customer']}. Feel free to shop again anytime!"
        )

    else:
        # Reply wasn't clear — send a nudge
        await send_message(
            phone,
            f"Sorry, we didn't understand your reply.\n\n"
            f"Please reply with:\n"
            f"✅ *YES* to confirm your order\n"
            f"❌ *NO* to cancel your order"
        )
        supabase.table("orders").update({"reply": text}).eq("order_id", order_id).execute()

    return {"status": "handled", "reply": reply or "unclear"}


# @router.get("/verify")
# async def verify_webhook(
#     hub_mode: str = None,
#     hub_challenge: str = None,
#     hub_verify_token: str = None,
# ):
#     """Meta requires this endpoint to verify the webhook URL."""
#     if hub_mode == "subscribe" and hub_verify_token == os.getenv("META_VERIFY_TOKEN"):
#         return int(hub_challenge)
#     return {"error": "verification failed"}
