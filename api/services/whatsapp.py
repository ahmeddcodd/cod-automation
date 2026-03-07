import os
import httpx

META_API_URL = "https://graph.facebook.com/v22.0"


async def send_confirmation(phone: str, order: dict) -> bool:
    token    = os.getenv("META_WHATSAPP_TOKEN")
    phone_id = os.getenv("META_PHONE_NUMBER_ID")

    # Explicit timeout for connect + read separately
    timeout = httpx.Timeout(
        connect=5.0,   # max 5s to establish connection
        read=10.0,     # max 10s to read response
        write=5.0,
        pool=5.0,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
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
            )
        print(f"WhatsApp response: {response.status_code} {response.text}")
        return response.status_code == 200
    except httpx.TimeoutException as e:
        print(f"WhatsApp timeout: {e}")
        return False
    except Exception as e:
        print(f"WhatsApp error: {e}")
        return False


async def send_message(phone: str, text: str) -> bool:
    token    = os.getenv("META_WHATSAPP_TOKEN")
    phone_id = os.getenv("META_PHONE_NUMBER_ID")

    timeout = httpx.Timeout(
        connect=5.0,
        read=10.0,
        write=5.0,
        pool=5.0,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
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
            )
        print(f"WhatsApp response: {response.status_code} {response.text}")
        return response.status_code == 200
    except httpx.TimeoutException as e:
        print(f"WhatsApp timeout: {e}")
        return False
    except Exception as e:
        print(f"WhatsApp error: {e}")
        return False
