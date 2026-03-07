import os
import httpx

META_API_URL = "https://graph.facebook.com/v22.0"


def _meta_timeout() -> httpx.Timeout:
    return httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


def _safe_text(value: object, fallback: str = "-") -> str:
    text = str(value or "").strip()
    return text if text else fallback


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _build_order_template(order: dict) -> dict:
    template_name = _safe_text(
        os.getenv("META_ORDER_TEMPLATE_NAME"),
        fallback="cod_order_confirmation",
    )
    template_lang = _safe_text(
        os.getenv("META_ORDER_TEMPLATE_LANG"),
        fallback="en_US",
    )

    # Meta's built-in hello_world template accepts no body components.
    if template_name == "hello_world":
        return {
            "name": template_name,
            "language": {"code": template_lang},
        }

    customer = _safe_text(order.get("customer"), fallback="Customer")
    order_name = _safe_text(order.get("order_name") or order.get("order_id"))
    product = _safe_text(order.get("product"), fallback="your order")
    quantity = _safe_text(order.get("quantity"), fallback="1")
    amount = _safe_text(order.get("amount"), fallback="0")
    currency = _safe_text(order.get("currency"), fallback="PKR")

    return {
        "name": template_name,
        "language": {"code": template_lang},
        "components": [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": customer},
                    {"type": "text", "text": order_name},
                    {"type": "text", "text": product},
                    {"type": "text", "text": quantity},
                    {"type": "text", "text": f"{currency} {amount}"},
                ],
            }
        ],
    }


def _build_fallback_template() -> dict:
    fallback_name = _safe_text(
        os.getenv("META_FALLBACK_TEMPLATE_NAME"),
        fallback="hello_world",
    )
    lang = _safe_text(
        os.getenv("META_ORDER_TEMPLATE_LANG"),
        fallback="en_US",
    )
    return {
        "name": fallback_name,
        "language": {"code": lang},
    }


def _template_missing_error(status_code: int, body_text: str) -> bool:
    if status_code != 404:
        return False
    lowered = body_text.lower()
    return "132001" in lowered or "template name does not exist" in lowered


async def _post_message(payload: dict) -> tuple[bool, int, str]:
    token = os.getenv("META_WHATSAPP_TOKEN")
    phone_id = os.getenv("META_PHONE_NUMBER_ID")

    if not token or not phone_id:
        return False, 0, "Missing META_WHATSAPP_TOKEN or META_PHONE_NUMBER_ID"

    try:
        async with httpx.AsyncClient(timeout=_meta_timeout()) as client:
            response = await client.post(
                f"{META_API_URL}/{phone_id}/messages",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        return response.status_code == 200, response.status_code, response.text
    except httpx.TimeoutException as e:
        return False, 0, f"timeout: {e}"
    except Exception as e:
        return False, 0, f"error: {e}"


async def send_confirmation(phone: str, order: dict) -> bool:
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": _build_order_template(order),
    }
    ok, status_code, body_text = await _post_message(payload)
    print(f"WhatsApp template response: {status_code} {body_text}")
    if ok:
        return True

    if _env_flag("META_TEMPLATE_FALLBACK_ENABLED", True) and _template_missing_error(status_code, body_text):
        fallback_payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "template",
            "template": _build_fallback_template(),
        }
        fb_ok, fb_status, fb_text = await _post_message(fallback_payload)
        print(f"WhatsApp fallback template response: {fb_status} {fb_text}")
        return fb_ok

    return False


async def send_message(phone: str, text: str) -> bool:
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": text},
    }
    ok, status_code, body_text = await _post_message(payload)
    print(f"WhatsApp text response: {status_code} {body_text}")
    return ok
