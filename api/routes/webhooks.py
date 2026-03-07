import asyncio
import hmac
import hashlib
import base64
import os
from fastapi import APIRouter, Request, HTTPException, Header
from api.db.supabase import get_supabase
from api.services.risk import calculate_risk
from api.services.whatsapp import send_confirmation
from api.services.inngest import trigger_confirmation_flow

router = APIRouter()


def verify_shopify_signature(body: bytes, hmac_header: str) -> bool:
    secret = os.getenv("SHOPIFY_WEBHOOK_SECRET", "").encode()
    digest = hmac.new(secret, body, hashlib.sha256).digest()
    computed = base64.b64encode(digest).decode()
    return hmac.compare_digest(computed, hmac_header or "")


def _insert_order_record(order_data: dict) -> None:
    supabase = get_supabase()
    supabase.table("orders").insert({
        "order_id":    order_data["order_id"],
        "order_name":  order_data["order_name"],
        "merchant_id": order_data["merchant_id"],
        "phone":       order_data["phone"],
        "customer":    order_data["customer"],
        "product":     order_data["product"],
        "amount":      order_data["amount"],
        "currency":    order_data["currency"],
        "risk_score":  order_data["risk_score"],
        "risk_flags":  order_data["risk_flags"],
        "status":      order_data.get("status", "pending"),
    }).execute()


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

    return " | ".join(values)


def _normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    # Common PK local format 03XXXXXXXXX -> convert to 92XXXXXXXXXX
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


@router.post("/shopify/order")
async def receive_order(
    request: Request,
    x_shopify_hmac_sha256: str = Header(None),
    x_merchant_id: str = Header(None),
):
    raw_body = await request.body()
    
    if os.getenv("VERIFY_SHOPIFY_SIGNATURE", "true") == "true":
        if not verify_shopify_signature(raw_body, x_shopify_hmac_sha256):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
        
    try:
        order = await request.json()
    except Exception:
        return {"status": "error", "reason": "invalid JSON payload"}

    print("Step 1: Order received")

    gateway_text = _extract_gateway_text(order)
    print(f"Step 1.5: Payment gateway = '{gateway_text}'")

    # Only handle COD orders
    is_cod = any(token in gateway_text for token in ("cod", "cash", "manual"))
    if not gateway_text:
        # Some Shopify payload variants omit gateway fields; don't skip blindly.
        print("Step 1.6: Gateway missing in payload; continuing as COD candidate")
        is_cod = True

    if not is_cod:
        return {"status": "skipped", "reason": "not a COD order"}

    billing = order.get("billing_address") if isinstance(order.get("billing_address"), dict) else {}
    phone = _extract_phone(order)
    customer_name = str(billing.get("first_name") or "Customer").strip() or "Customer"
    items = order.get("line_items", [])

    merchant_id = (
        (x_merchant_id or "").strip()
        or (os.getenv("DEFAULT_MERCHANT_ID", "").strip() or None)
        or (str(order.get("shop_id", "")).strip() or None)
    )

    order_data = {
        "order_id":    str(order["id"]),
        "order_name":  order.get("name", ""),
        "merchant_id": merchant_id,
        "phone":       phone,
        "customer":    customer_name,
        "product":     items[0]["name"] if items else "your order",
        "quantity":    items[0]["quantity"] if items else 1,
        "amount":      order.get("total_price", "0"),
        "currency":    order.get("currency", "PKR"),
        "status":      "pending",
    }

    if not order_data["phone"] or len(order_data["phone"]) < 10:
        print("Step 1.7: Missing phone in Shopify payload; saving order as skipped")
        order_data["phone"] = f"missing-{order_data['order_id']}"
        order_data["status"] = "skipped_missing_phone"
        order_data["risk_score"] = 0.0
        order_data["risk_flags"] = ["missing_phone"]
        try:
            await asyncio.wait_for(asyncio.to_thread(_insert_order_record, order_data), timeout=8)
            print("Step 1.8: Order saved to Supabase (skipped_missing_phone)")
        except Exception as e:
            print(f"Step 1.9: Failed to save skipped order: {e}")
            return {"status": "error", "reason": str(e)}
        return {
            "status": "skipped",
            "reason": "missing phone number",
            "order_id": order_data["order_id"],
        }

    print(f"Step 2: Running risk check for {order_data['phone']}")

    # Risk check
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

    # Save to Supabase
    try:
        await asyncio.wait_for(asyncio.to_thread(_insert_order_record, order_data), timeout=8)
        print("Step 4: Order saved to Supabase")
    except asyncio.TimeoutError:
        print("Supabase insert timed out")
        return {"status": "error", "reason": "supabase insert timeout"}
    except Exception as e:
        message = str(e)
        if order_data.get("merchant_id") and "foreign key" in message.lower():
            try:
                print("Step 4.1: Merchant FK failed, retrying with null merchant_id")
                order_data["merchant_id"] = None
                await asyncio.wait_for(asyncio.to_thread(_insert_order_record, order_data), timeout=8)
                print("Step 4.2: Order saved to Supabase with null merchant_id")
            except Exception as retry_error:
                print(f"Supabase retry failed: {retry_error}")
                return {"status": "error", "reason": str(retry_error)}
        else:
            print(f"Supabase insert failed: {e}")
            return {"status": "error", "reason": message}

    # Send WhatsApp
    try:
        await send_confirmation(order_data["phone"], order_data)
        print("Step 5: WhatsApp sent")
    except Exception as e:
        print(f"WhatsApp failed: {e}")

    # Fire Inngest
    try:
        await trigger_confirmation_flow(order_data)
        print("Step 6: Inngest triggered")
    except Exception as e:
        print(f"Inngest failed: {e}")

    print("Step 7: Done")
    return {"status": "processing", "order_id": order_data["order_id"]}
