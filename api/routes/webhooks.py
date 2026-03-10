import asyncio
import base64
import hashlib
import hmac
import os

from fastapi import APIRouter, Header, HTTPException, Request
from api.db.supabase import get_supabase
from api.services.inngest import trigger_confirmation_flow
from api.services.risk import calculate_risk
from api.services.risk_decision import make_order_decision
from api.services.whatsapp import send_confirmation, send_message

router = APIRouter()

# Payment gateway strings that indicate a COD order
COD_TOKENS = ("cod", "cash", "manual", "cash on delivery")


# ── Helpers ────────────────────────────────────────────────────────────────

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
    payload  = {
        "order_id":       order_data["order_id"],
        "order_name":     order_data["order_name"],
        "merchant_id":    order_data["merchant_id"],
        "phone":          order_data["phone"],
        "customer":       order_data["customer"],
        "product":        order_data["product"],
        "amount":         order_data["amount"],
        "currency":       order_data["currency"],
        "risk_score":     order_data.get("risk_score", 0.0),
        "risk_flags":     order_data.get("risk_flags", []),
        "risk_verdict":   order_data.get("risk_verdict", "low_risk"),
        "risk_decision":  order_data.get("risk_decision", "proceed"),
        "status":         order_data.get("status", "pending"),
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
    return " | ".join(values)


def _normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("03"):
        digits = "92" + digits[1:]
    return digits


def _extract_phone(order: dict) -> str:
    billing  = order.get("billing_address")  if isinstance(order.get("billing_address"),  dict) else {}
    shipping = order.get("shipping_address") if isinstance(order.get("shipping_address"), dict) else {}
    customer = order.get("customer")         if isinstance(order.get("customer"),         dict) else {}
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
    billing  = order.get("billing_address")  if isinstance(order.get("billing_address"),  dict) else {}
    shipping = order.get("shipping_address") if isinstance(order.get("shipping_address"), dict) else {}
    customer = order.get("customer")         if isinstance(order.get("customer"),         dict) else {}

    for source in (billing, shipping, customer):
        first = str(source.get("first_name") or "").strip()
        last  = str(source.get("last_name")  or "").strip()
        full  = " ".join(p for p in (first, last) if p).strip()
        if full:
            return full
    return "Customer"


def _extract_product_and_qty(order: dict) -> tuple[str, int]:
    items      = order.get("line_items") if isinstance(order.get("line_items"), list) else []
    first_item = items[0] if items and isinstance(items[0], dict) else {}

    product  = str(first_item.get("name") or first_item.get("title") or "your order").strip()
    quantity = sum(
        max(int(item.get("quantity", 1)), 1)
        for item in items
        if isinstance(item, dict)
    ) or 1
    return product or "your order", quantity


def _flatten_address(addr: dict) -> str:
    parts = [
        addr.get("address1"), addr.get("address2"),
        addr.get("city"),     addr.get("province"),
        addr.get("zip"),      addr.get("country"),
    ]
    return ", ".join(str(p).strip() for p in parts if str(p or "").strip())


def _extract_address(order: dict) -> str:
    for key in ("shipping_address", "billing_address"):
        addr = order.get(key)
        if isinstance(addr, dict):
            text = _flatten_address(addr)
            if text:
                return text
    return ""


def _extract_amount(order: dict) -> str:
    raw = (
        order.get("total_price")
        or order.get("current_total_price")
        or order.get("subtotal_price")
        or "0"
    )
    return str(raw).strip() or "0"


def _extract_currency(order: dict) -> str:
    value = (
        order.get("currency")
        or order.get("presentment_currency")
        or order.get("shop_currency")
        or "PKR"
    )
    return str(value).strip().upper() or "PKR"


def _build_order_data(order: dict, shop_domain: str) -> dict:
    order_id = order.get("id")
    if order_id in (None, ""):
        raise ValueError("missing order id")

    product, quantity = _extract_product_and_qty(order)
    return {
        "order_id":    str(order_id),
        "order_name":  str(order.get("name") or "").strip(),
        "merchant_id": shop_domain,   # ← always the Shopify shop domain
        "phone":       _extract_phone(order),
        "customer":    _extract_customer_name(order),
        "product":     product,
        "quantity":    quantity,
        "address":     _extract_address(order),
        "amount":      _extract_amount(order),
        "currency":    _extract_currency(order),
        "created_at":  str(order.get("created_at") or order.get("processed_at") or "").strip(),
        "status":      "pending",
    }


def _is_cod_order(gateway_text: str) -> bool:
    if any(token in gateway_text for token in COD_TOKENS):
        return True
    if not gateway_text and _env_flag("ASSUME_COD_WHEN_GATEWAY_MISSING", False):
        return True
    return False


async def _fetch_store_name(merchant_id: str | None) -> str:
    if not merchant_id:
        return "Our Store"
    try:
        supabase = get_supabase()
        result   = (
            supabase.table("merchants")
            .select("store_name")
            .eq("merchant_id", merchant_id)
            .execute()
        )
        return result.data[0]["store_name"] if result.data else "Our Store"
    except Exception:
        return "Our Store"


async def _notify_merchant(merchant_id: str | None, order_data: dict, risk: dict, reason: str) -> None:
    """Alert the merchant's WhatsApp about a suspicious order."""
    if not merchant_id:
        return
    try:
        supabase = get_supabase()
        result   = supabase.table("merchants").select("phone").eq("merchant_id", merchant_id).execute()
        merchant_phone = result.data[0].get("phone", "") if result.data else ""
        if not merchant_phone:
            return
        flags_text = ", ".join(risk.get("flags", [])) or "none"
        await send_message(
            merchant_phone,
            f"⚠️ *Suspicious Order Alert*\n\n"
            f"Order: {order_data.get('order_name', '')}\n"
            f"Customer: {order_data.get('customer', '')}\n"
            f"Phone: {order_data.get('phone', '')}\n"
            f"Amount: {order_data.get('currency', '')} {order_data.get('amount', '')}\n"
            f"Risk Score: {risk.get('score', 0.0)}\n"
            f"Flags: {flags_text}\n"
            f"Reason: {reason}\n\n"
            f"WhatsApp confirmation has been sent to the customer."
        )
    except Exception as e:
        print(f"Merchant notification failed: {e}")


# ── Main webhook handler ───────────────────────────────────────────────────

@router.post("/shopify/order")
async def receive_order(
    request: Request,
    x_shopify_hmac_sha256: str | None = Header(default=None),
    x_shopify_shop_domain: str | None = Header(default=None),  # ← Shopify sends this automatically
):
    raw_body = await request.body()

    if _env_flag("VERIFY_SHOPIFY_SIGNATURE", True):
        if not verify_shopify_signature(raw_body, x_shopify_hmac_sha256):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # ── Guard: shop domain must be present ────────────────────────────────
    if not x_shopify_shop_domain:
        return {"status": "error", "reason": "missing x-shopify-shop-domain header"}

    try:
        order = await request.json()
    except Exception:
        return {"status": "error", "reason": "invalid JSON payload"}

    if not isinstance(order, dict):
        return {"status": "error", "reason": "invalid Shopify payload shape"}

    print("Step 1: Order received")

    gateway_text = _extract_gateway_text(order)
    print(f"Step 1.5: Payment gateway = '{gateway_text}'")

    if not _is_cod_order(gateway_text):
        return {"status": "skipped", "reason": "not a COD order"}

    try:
        order_data = _build_order_data(order, x_shopify_shop_domain)  # ← pass shop domain
    except ValueError as e:
        return {"status": "error", "reason": str(e)}

    order_data["store_name"] = await _fetch_store_name(order_data["merchant_id"])

    # ── Missing phone — save and skip ─────────────────────────────────────
    if not order_data["phone"] or len(order_data["phone"]) < 10:
        print("Step 1.7: Missing phone")
        order_data.update({
            "phone":         f"missing-{order_data['order_id']}",
            "status":        "skipped_missing_phone",
            "risk_score":    0.0,
            "risk_flags":    ["phone_missing"],
            "risk_verdict":  "high_risk",
            "risk_decision": "auto_reject",
        })
        try:
            await asyncio.wait_for(asyncio.to_thread(_upsert_order_record, order_data), timeout=8)
        except Exception as e:
            return {"status": "error", "reason": str(e)}
        return {"status": "skipped", "reason": "missing phone number", "order_id": order_data["order_id"]}

    print(f"Step 2: Running risk check for {order_data['phone']}")

    # ── Risk scoring ──────────────────────────────────────────────────────
    risk: dict = {}
    try:
        risk = await asyncio.wait_for(calculate_risk(order_data), timeout=8)
        order_data["risk_score"]   = risk["score"]
        order_data["risk_flags"]   = risk["flags"]
        order_data["risk_verdict"] = risk["verdict"]
        print(f"Step 3: Risk score={risk['score']} verdict={risk['verdict']} flags={risk['flags']}")
    except asyncio.TimeoutError:
        print("Step 3: Risk check timed out — defaulting to low_risk")
        risk = {"score": 0.0, "verdict": "low_risk", "flags": [], "breakdown": {},
                "signal_context": {}, "past_order_count": 0, "confirmed_count": 0,
                "cancelled_count": 0, "address_used": ""}
        order_data.update({"risk_score": 0.0, "risk_flags": [], "risk_verdict": "low_risk"})
    except Exception as e:
        print(f"Step 3: Risk check failed: {e} — defaulting to low_risk")
        risk = {"score": 0.0, "verdict": "low_risk", "flags": [], "breakdown": {},
                "signal_context": {}, "past_order_count": 0, "confirmed_count": 0,
                "cancelled_count": 0, "address_used": ""}
        order_data.update({"risk_score": 0.0, "risk_flags": [], "risk_verdict": "low_risk"})

    # ── LLM order decision ────────────────────────────────────────────────
    decision_result = {"decision": "proceed", "reason": "default", "source": "rules"}
    try:
        decision_result = await asyncio.wait_for(
            make_order_decision(order_data, risk), timeout=8
        )
        order_data["risk_decision"] = decision_result["decision"]
        print(f"Step 3.5: Decision={decision_result['decision']} source={decision_result['source']} | {decision_result['reason']}")
    except asyncio.TimeoutError:
        print("Step 3.5: Order decision timed out — proceeding")
        order_data["risk_decision"] = "proceed"
    except Exception as e:
        print(f"Step 3.5: Order decision failed: {e} — proceeding")
        order_data["risk_decision"] = "proceed"

    # ── Save to Supabase ──────────────────────────────────────────────────
    try:
        await asyncio.wait_for(asyncio.to_thread(_upsert_order_record, order_data), timeout=8)
        print("Step 4: Order saved to Supabase")
    except Exception as e:
        print(f"Supabase save failed: {e}")
        return {"status": "error", "reason": str(e)}

    decision = decision_result["decision"]

    # ── Act on decision ───────────────────────────────────────────────────
    if decision == "auto_reject":
        try:
            supabase = get_supabase()
            supabase.table("orders").update({"status": "auto_rejected"}).eq(
                "order_id", order_data["order_id"]
            ).execute()
        except Exception as e:
            print(f"Auto-reject DB update failed: {e}")
        print("Step 5: Order auto-rejected — no WhatsApp sent")
        return {"status": "auto_rejected", "order_id": order_data["order_id"]}

    # Send WhatsApp confirmation (both proceed and flag_for_review)
    try:
        sent = await send_confirmation(order_data["phone"], order_data)
        print(f"Step 5: WhatsApp {'sent' if sent else 'failed'}")
    except Exception as e:
        print(f"WhatsApp failed: {e}")

    # Alert merchant if flagged
    if decision == "flag_for_review":
        try:
            await _notify_merchant(
                order_data["merchant_id"], order_data, risk, decision_result["reason"]
            )
            print("Step 5.5: Merchant notified")
        except Exception as e:
            print(f"Merchant notification failed: {e}")

    # ── Trigger Inngest wait-and-cancel flow ──────────────────────────────
    try:
        await trigger_confirmation_flow(order_data)
        print("Step 6: Inngest triggered")
    except Exception as e:
        print(f"Inngest failed: {e}")

    print("Step 7: Done")
    return {"status": "processing", "order_id": order_data["order_id"]}