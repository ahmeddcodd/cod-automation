import os
import httpx

META_API_URL = "https://graph.facebook.com/v18.0"


async def send_confirmation(phone: str, order: dict) -> bool:
    token    = os.getenv("META_WHATSAPP_TOKEN")
    phone_id = os.getenv("META_PHONE_NUMBER_ID")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{META_API_URL}/{phone_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "template",
                "template": {
                    "name": "hello_world",
                    "language": {"code": "en_US"}
                }
            },
            timeout=10,
        )
    return response.status_code == 200


async def send_message(phone: str, text: str) -> bool:
    """Send any plain text WhatsApp message."""
    token    = os.getenv("META_WHATSAPP_TOKEN")
    phone_id = os.getenv("META_PHONE_NUMBER_ID")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{META_API_URL}/{phone_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messaging_product": "whatsapp",
                "to":   phone,
                "type": "text",
                "text": {"body": text},
            },
            timeout=10,
        )
    return response.status_code == 200


def _build_message(order: dict) -> str:
    name     = order.get("customer", "Customer")
    product  = order.get("product", "your order")
    amount   = order.get("amount", "")
    currency = order.get("currency", "PKR")
    qty      = order.get("quantity", 1)

    return (
        f"Hi {name}! 👋\n\n"
        f"We received your *Cash on Delivery* order:\n\n"
        f"📦 *{product}*\n"
        f"🔢 Qty: {qty}\n"
        f"💰 Amount: *{currency} {amount}*\n\n"
        f"Please confirm your order:\n"
        f"✅ Reply *YES* to confirm\n"
        f"❌ Reply *NO* to cancel\n\n"
        f"⏳ If we don't hear back in 20 minutes, the order will be *automatically cancelled*."
    )
