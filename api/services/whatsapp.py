import os
import httpx

META_API_URL = "https://graph.facebook.com/v22.0"


async def send_confirmation(phone: str, order: dict) -> bool:
    token    = os.getenv("META_WHATSAPP_TOKEN")
    phone_id = os.getenv("META_PHONE_NUMBER_ID")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{META_API_URL}/{phone_id}/messages",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
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
    print(f"WhatsApp response: {response.status_code} {response.text}")
    return response.status_code == 200


async def send_message(phone: str, text: str) -> bool:
    token    = os.getenv("META_WHATSAPP_TOKEN")
    phone_id = os.getenv("META_PHONE_NUMBER_ID")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{META_API_URL}/{phone_id}/messages",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "text",
                "text": {"body": text}
            },
            timeout=10,
        )
    print(f"WhatsApp response: {response.status_code} {response.text}")
    return response.status_code == 200
