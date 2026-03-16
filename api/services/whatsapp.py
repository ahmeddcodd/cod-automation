import os
import httpx

META_API_URL = "https://graph.facebook.com/v22.0"
DEFAULT_ORDER_TEMPLATE_NAME = "_cod_order_confirmation"
DEFAULT_TEMPLATE_LANG = "en"
DEFAULT_TEMPLATE_LANG_VARIANTS = ("en", "en_US", "en_GB")
TEMPLATE_NAME_ALIASES = {
    "_cod_order_confirmation": DEFAULT_ORDER_TEMPLATE_NAME,
    "cod_order_confirmation": DEFAULT_ORDER_TEMPLATE_NAME,
    "hello_world": DEFAULT_ORDER_TEMPLATE_NAME,
}


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


def _resolve_template_name(raw_name: object, fallback: str) -> str:
    name = _safe_text(raw_name, fallback=fallback)
    return TEMPLATE_NAME_ALIASES.get(name.strip().lower(), name.strip())


def _normalize_template_lang(raw_lang: object, fallback: str = DEFAULT_TEMPLATE_LANG) -> str:
    value = _safe_text(raw_lang, fallback=fallback).strip()
    lowered = value.lower().replace("-", "_")

    lang_aliases = {
        "english": "en",
        "en": "en",
        "en_us": "en_US",
        "enus": "en_US",
        "en_gb": "en_GB",
        "engb": "en_GB",
    }
    return lang_aliases.get(lowered, value)


def _template_name_variants(base_name: str) -> list[str]:
    clean = str(base_name or "").strip()
    variants: list[str] = []

    def _add(value: str) -> None:
        value = str(value or "").strip()
        if value and value not in variants:
            variants.append(value)

    _add(clean)
    _add(clean.lstrip("_"))
    if clean and not clean.startswith("_"):
        _add(f"_{clean}")
    return variants


def _template_lang_variants() -> list[str]:
    variants: list[str] = []

    def _add(value: str) -> None:
        value = _normalize_template_lang(value, fallback=DEFAULT_TEMPLATE_LANG)
        if value and value not in variants:
            variants.append(value)

    _add(os.getenv("META_ORDER_TEMPLATE_LANG"))
    for default_lang in DEFAULT_TEMPLATE_LANG_VARIANTS:
        _add(default_lang)

    raw_fallbacks = str(os.getenv("META_ORDER_TEMPLATE_LANG_FALLBACKS") or "")
    for item in raw_fallbacks.split(","):
        _add(item)

    return variants


def _clone_template_with_name_and_lang(template: dict, name: str, lang: str) -> dict:
    updated = dict(template)
    updated["name"] = str(name).strip()
    updated["language"] = {"code": str(lang).strip()}
    return updated


def _build_order_template(order: dict) -> dict:
    template_name = _resolve_template_name(
        os.getenv("META_ORDER_TEMPLATE_NAME"),
        fallback=DEFAULT_ORDER_TEMPLATE_NAME,
    )
    template_lang = _normalize_template_lang(
        os.getenv("META_ORDER_TEMPLATE_LANG"),
        fallback=DEFAULT_TEMPLATE_LANG,
    )

    store_name = order.get("store_name", "Our Store")
    customer   = order.get("customer", "Customer")
    order_name = order.get("order_name", "")
    product    = order.get("product", "your order")
    quantity   = str(order.get("quantity", 1))
    amount     = order.get("amount", "0")
    currency   = order.get("currency", "PKR")

    return {
        "name": template_name,
        "language": {"code": template_lang},
        "components": [
            {
                "type": "header",
                "parameters": [
                    {"type": "text", "text": store_name}
                ]
            }
            ,
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
    fallback_name = _resolve_template_name(
        os.getenv("META_FALLBACK_TEMPLATE_NAME"),
        fallback=DEFAULT_ORDER_TEMPLATE_NAME,
    )
    lang = _normalize_template_lang(
        os.getenv("META_ORDER_TEMPLATE_LANG"),
        fallback=DEFAULT_TEMPLATE_LANG,
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


async def _post_message(
    payload: dict,
    token: str | None = None,
    phone_id: str | None = None,
) -> tuple[bool, int, str]:
    token = token or os.getenv("META_WHATSAPP_TOKEN")
    phone_id = phone_id or os.getenv("META_PHONE_NUMBER_ID")

    if not token or not phone_id:
        return False, 0, "Missing WhatsApp token or phone number ID"

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


def _get_merchant_wa_creds(merchant_id: str | None) -> tuple[str | None, str | None]:
    """Look up per-merchant WhatsApp credentials from the DB."""
    if not merchant_id:
        return None, None
    try:
        from api.db.supabase import get_supabase
        supabase = get_supabase()
        result = (
            supabase.table("merchants")
            .select("wa_access_token, wa_phone_number_id")
            .eq("merchant_id", merchant_id)
            .limit(1)
            .execute()
        )
        if result.data:
            row = result.data[0]
            token = row.get("wa_access_token")
            pid = row.get("wa_phone_number_id")
            if token and pid:
                return token, pid
    except Exception as e:
        print(f"Failed to fetch merchant WA creds: {e}")
    return None, None


async def send_confirmation(phone: str, order: dict, merchant_id: str | None = None) -> bool:
    token, phone_id = _get_merchant_wa_creds(merchant_id)
    selected_template = _build_order_template(order)
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": selected_template,
    }
    print(
        "WhatsApp primary template selected: "
        f"{selected_template.get('name')} [{selected_template.get('language', {}).get('code')}]"
        f" (merchant={merchant_id}, per-merchant={'yes' if token else 'no'})"
    )
    ok, status_code, body_text = await _post_message(payload, token=token, phone_id=phone_id)
    print(f"WhatsApp template response: {status_code} {body_text}")
    if ok:
        return True

    if _template_missing_error(status_code, body_text):
        tried: set[tuple[str, str]] = set()
        base_name = str(selected_template.get("name") or "")
        base_lang = str(selected_template.get("language", {}).get("code") or DEFAULT_TEMPLATE_LANG)
        tried.add((base_name, base_lang))

        for candidate_name in _template_name_variants(base_name):
            for candidate_lang in _template_lang_variants():
                key = (candidate_name, candidate_lang)
                if key in tried:
                    continue
                tried.add(key)

                candidate_template = _clone_template_with_name_and_lang(
                    selected_template,
                    name=candidate_name,
                    lang=candidate_lang,
                )
                retry_payload = {
                    "messaging_product": "whatsapp",
                    "to": phone,
                    "type": "template",
                    "template": candidate_template,
                }
                print(
                    "WhatsApp template retry selected: "
                    f"{candidate_template.get('name')} [{candidate_template.get('language', {}).get('code')}]"
                )
                retry_ok, retry_status, retry_body = await _post_message(retry_payload, token=token, phone_id=phone_id)
                print(f"WhatsApp template retry response: {retry_status} {retry_body}")
                if retry_ok:
                    return True

    if _env_flag("META_TEMPLATE_FALLBACK_ENABLED", False) and _template_missing_error(status_code, body_text):
        fallback_template = _build_fallback_template()
        fallback_payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "template",
            "template": fallback_template,
        }
        print(f"WhatsApp fallback template selected: {fallback_template.get('name')}")
        fb_ok, fb_status, fb_text = await _post_message(fallback_payload, token=token, phone_id=phone_id)
        print(f"WhatsApp fallback template response: {fb_status} {fb_text}")
        return fb_ok

    return False


async def send_message(phone: str, text: str, merchant_id: str | None = None) -> bool:
    token, phone_id = _get_merchant_wa_creds(merchant_id)
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": text},
    }
    ok, status_code, body_text = await _post_message(payload, token=token, phone_id=phone_id)
    print(f"WhatsApp text response: {status_code} {body_text}")
    return ok
