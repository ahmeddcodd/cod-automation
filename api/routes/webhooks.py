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


@router.post("/shopify/order")
async def receive_order(
    request: Request,
    x_shopify_hmac_sha256: str = Header(None),
    x_merchant_id: str = Header(None),
):
    raw_body = await request.body()

    # Verify it's genuinely from Shopify
    if not verify_shopify_signature(raw_body, x_shopify_hmac_sha256):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    order = await request.json()

    # Only handle COD orders
    gateway = order.get("payment_gateway", "").lower()
    if "cod" not in gateway and "cash" not in gateway:
        return {"status": "skipped", "reason": "not a COD order"}

    # Build a clean order object
    billing = order.get("billing_address") or {}
    phone   = order.get("phone") or billing.get("phone", "")
    items   = order.get("line_items", [])

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

    # Skip if no phone number
    if not order_data["phone"] or len(order_data["phone"]) < 10:
        return {"status": "skipped", "reason": "missing phone number"}

    # Risk check
    risk = await calculate_risk(order_data)
    order_data["risk_score"] = risk["score"]
    order_data["risk_flags"] = risk["flags"]

    # Save to Supabase
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

    # Always send WhatsApp first — even for high risk orders
    await send_confirmation(order_data["phone"], order_data)

    # Kick off background wait → auto-cancel flow
    await trigger_confirmation_flow(order_data)

    return {"status": "processing", "order_id": order_data["order_id"]}
