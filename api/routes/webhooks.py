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
        "status":      "pending",
    }).execute()


@router.post("/shopify/order")
async def receive_order(
    request: Request,
    x_shopify_hmac_sha256: str = Header(None),
    x_merchant_id: str = Header(None),
):
    raw_body = await request.body()
    try:
        order = await request.json()
    except Exception:
        return {"status": "error", "reason": "invalid JSON payload"}

    print("Step 1: Order received")

    # Only handle COD orders
    gateway = order.get("payment_gateway", "").lower()
    if "cod" not in gateway and "cash" not in gateway:
        return {"status": "skipped", "reason": "not a COD order"}

    billing  = order.get("billing_address") or {}
    phone    = order.get("phone") or billing.get("phone", "")
    items    = order.get("line_items", [])

    order_data = {
        "order_id":    str(order["id"]),
        "order_name":  order.get("name", ""),
        "merchant_id": x_merchant_id or str(order.get("shop_id", "")),
        "phone":       phone.strip(),
        "customer":    billing.get("first_name", "Customer"),
        "product":     items[0]["name"] if items else "your order",
        "quantity":    items[0]["quantity"] if items else 1,
        "amount":      order.get("total_price", "0"),
        "currency":    order.get("currency", "PKR"),
    }

    if not order_data["phone"] or len(order_data["phone"]) < 10:
        return {"status": "skipped", "reason": "missing phone number"}

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
        print(f"Supabase insert failed: {e}")
        return {"status": "error", "reason": str(e)}

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
