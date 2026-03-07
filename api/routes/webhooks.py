import asyncio
import base64
import hashlib
import hmac
import os
from fastapi import APIRouter, Header, HTTPException, Request
from api.db.supabase import get_supabase
from api.services.inngest import trigger_confirmation_flow
from api.services.risk import calculate_risk
from api.services.whatsapp import send_confirmation

router = APIRouter()

COD_TOKENS = ("cod", "cash", "manual", "cash on delivery")


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def verify_shopify_signature(body: bytes, hmac_header: str | None) -> bool:
    secret = os.getenv("SHOPIFY_WEBHOOK_SECRET", "").strip()
    if not secret:
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    computed = base64.b64encode(digest).decode()
    return hmac.compare_digest(computed, hmac_header or "")


def _upsert_order_record(order_data: dict) -> None:
    supabase = get_supabase()
    payload = {
        "order_id": order_data["order_id"],
        "order_name": order_data["order_name"],
        "merchant_id": order_data["merchant_id"],
        "phone": order_data["phone"],
        "customer": order_data["customer"],
        "product": order_data["product"],
        "amount": order_data["amount"],
        "currency": order_data["currency"],
        "risk_score": order_data["risk_score"],
        "risk_flags": order_data["risk_flags"],
        "status": order_data.get("status", "pending"),
    }

    existing = (
        supabase.table("orders")
        .select("id")
        .eq("order_id", payload["order_id"])
        .limit(1)
        .execute()
        .data
    )
    if existing:
        supabase.table("orders").update(payload).eq("order_id", payload["order_id"]).execute()
    else:
        supabase.table("orders").insert(payload).execute()


def _extract_gateway_text(order: dict) -> str:
    values: list[str] = []

    for key in ("payment_gateway", "gateway", "payment_method", "processing_method"):
        value = order.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip().lower())

    gateway_names = order.get("payment_gateway_names")
    if isinstance(gateway_names, list):
        values.extend(str(v).strip().lower() for v in gateway_names if str(v).strip())

    payment_details = order.get("payment_details")
    if isinstance(payment_details, dict):
        company = payment_details.get("credit_card_company")
        if isinstance(company, str) and company.strip():
            values.append(company.strip().lower())

    tags = order.get("tags")
    if isinstance(tags, str) and tags.strip():
        values.append(tags.strip().lower())

    return " | ".join(values)


def _normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("03"):
        digits = "92" + digits[1:]
    return digits


def _extract_phone(order: dict) -> str:
    billing = order.get("billing_address") if isinstance(order.get("billing_address"), dict) else {}
    shipping = order.get("shipping_address") if isinstance(order.get("shipping_address"), dict) else {}
    customer = order.get("customer") if isinstance(order.get("customer"), dict) else {}
    default_address = customer.get("default_address") if isinstance(customer.get("default_address"), dict) else {}

    candidates = [
        order.get("phone"),
        billing.get("phone"),
        shipping.get("phone"),
        customer.get("phone"),
        default_address.get("phone"),
    ]

    for candidate in candidates:
        if candidate is None:
            continue
        normalized = _normalize_phone(str(candidate).strip())
        if len(normalized) >= 10:
            return normalized
    return ""


def _extract_customer_name(order: dict) -> str:
    billing = order.get("billing_address") if isinstance(order.get("billing_address"), dict) else {}
    shipping = order.get("shipping_address") if isinstance(order.get("shipping_address"), dict) else {}
    customer = order.get("customer") if isinstance(order.get("customer"), dict) else {}

    for source in (billing, shipping, customer):
        first = str(source.get("first_name") or "").strip()
        last = str(source.get("last_name") or "").strip()
        full_name = " ".join(part for part in (first, last) if part).strip()
        if full_name:
            return full_name

    return "Customer"


def _extract_product_and_qty(order: dict) -> tuple[str, int]:
    items = order.get("line_items") if isinstance(order.get("line_items"), list) else []
    first_item = items[0] if items and isinstance(items[0], dict) else {}

    product = str(first_item.get("name") or first_item.get("title") or "your order").strip()
    quantity_raw = first_item.get("quantity", 1)
    try:
        quantity = int(quantity_raw)
    except (TypeError, ValueError):
        quantity = 1

    if quantity < 1:
        quantity = 1

    return product or "your order", quantity


def _extract_amount(order: dict) -> str:
    raw_amount = (
        order.get("total_price")
        or order.get("current_total_price")
        or order.get("subtotal_price")
        or "0"
    )
    return str(raw_amount).strip() or "0"


def _extract_currency(order: dict) -> str:
    value = (
        order.get("currency")
        or order.get("presentment_currency")
        or order.get("shop_currency")
        or "PKR"
    )
    return str(value).strip().upper() or "PKR"


def _build_order_data(order: dict, merchant_header: str | None) -> dict:
    order_id_raw = order.get("id")
    if order_id_raw in (None, ""):
        raise ValueError("missing order id")

    merchant_id = (
        (merchant_header or "").strip()
        or (os.getenv("DEFAULT_MERCHANT_ID", "").strip() or None)
    )

    product, quantity = _extract_product_and_qty(order)
    return {
        "order_id": str(order_id_raw),
        "order_name": str(order.get("name") or "").strip(),
        "merchant_id": merchant_id,
        "phone": _extract_phone(order),
        "customer": _extract_customer_name(order),
        "product": product,
        "quantity": quantity,
        "amount": _extract_amount(order),
        "currency": _extract_currency(order),
        "status": "pending",
    }


def _is_cod_order(order: dict, gateway_text: str) -> bool:
    if any(token in gateway_text for token in COD_TOKENS):
        return True
    if not gateway_text and _env_flag("ASSUME_COD_WHEN_GATEWAY_MISSING", False):
        return True
    return False


@router.post("/shopify/order")
async def receive_order(
    request: Request,
    x_shopify_hmac_sha256: str | None = Header(default=None),
    x_merchant_id: str | None = Header(default=None),
):
    raw_body = await request.body()

    if _env_flag("VERIFY_SHOPIFY_SIGNATURE", True):
        if not verify_shopify_signature(raw_body, x_shopify_hmac_sha256):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        order = await request.json()
    except Exception:
        return {"status": "error", "reason": "invalid JSON payload"}

    if not isinstance(order, dict):
        return {"status": "error", "reason": "invalid Shopify payload shape"}

    print("Step 1: Order received")

    gateway_text = _extract_gateway_text(order)
    print(f"Step 1.5: Payment gateway = '{gateway_text}'")

    if not _is_cod_order(order, gateway_text):
        return {"status": "skipped", "reason": "not a COD order"}

    if not gateway_text:
        print("Step 1.6: Gateway missing, continuing due to ASSUME_COD_WHEN_GATEWAY_MISSING=true")

    try:
        order_data = _build_order_data(order, x_merchant_id)
    except ValueError as e:
        return {"status": "error", "reason": str(e)}

    # Add store_name for template header
    try:
        supabase = get_supabase()
        merchant = (
            supabase.table("merchants")
            .select("store_name")
            .eq("merchant_id", order_data["merchant_id"])
            .execute()
        )
        store_name = merchant.data[0]["store_name"] if merchant.data else "Our Store"
        order_data["store_name"] = store_name
    except Exception:
        order_data["store_name"] = "Our Store"

    if not order_data["phone"] or len(order_data["phone"]) < 10:
        print("Step 1.7: Missing phone; saving as skipped_missing_phone")
        order_data["phone"] = f"missing-{order_data['order_id']}"
        order_data["status"] = "skipped_missing_phone"
        order_data["risk_score"] = 0.0
        order_data["risk_flags"] = ["missing_phone"]
        try:
            await asyncio.wait_for(asyncio.to_thread(_upsert_order_record, order_data), timeout=8)
            print("Step 1.8: Saved skipped_missing_phone order to Supabase")
        except Exception as e:
            print(f"Step 1.9: Failed to save skipped order: {e}")
            return {"status": "error", "reason": str(e)}
        return {
            "status": "skipped",
            "reason": "missing phone number",
            "order_id": order_data["order_id"],
        }

    print(f"Step 2: Running risk check for {order_data['phone']}")

    try:
        risk = await asyncio.wait_for(calculate_risk(order_data), timeout=8)
        order_data["risk_score"] = risk["score"]
        order_data["risk_flags"] = risk["flags"]
        print(f"Step 3: Risk score = {risk['score']}")
    except asyncio.TimeoutError:
        print("Risk check timed out")
        order_data["risk_score"] = 0.0
        order_data["risk_flags"] = []
    except Exception as e:
        print(f"Risk check failed: {e}")
        order_data["risk_score"] = 0.0
        order_data["risk_flags"] = []

    try:
        await asyncio.wait_for(asyncio.to_thread(_upsert_order_record, order_data), timeout=8)
        print("Step 4: Order saved to Supabase")
    except Exception as e:
        print(f"Supabase save failed: {e}")
        return {"status": "error", "reason": str(e)}

    try:
        sent = await send_confirmation(order_data["phone"], order_data)
        if sent:
            print("Step 5: WhatsApp sent")
        else:
            print("Step 5: WhatsApp send failed")
    except Exception as e:
        print(f"WhatsApp failed: {e}")

    try:
        await trigger_confirmation_flow(order_data)
        print("Step 6: Inngest triggered")
    except Exception as e:
        print(f"Inngest failed: {e}")

    print("Step 7: Done")
    return {"status": "processing", "order_id": order_data["order_id"]}
