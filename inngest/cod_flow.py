"""
COD Automation Flow — Phase 1

Simple, reliable, no AI involved:
  1. Wait for customer reply (20 mins default)
  2. Check if they replied
  3. If no reply → auto cancel on Shopify
"""

import os
from inngest import Inngest, TriggerEvent
from api.db.supabase import get_supabase
from api.services.shopify import cancel_order
from api.services.whatsapp import send_message

inngest_client = Inngest(
    app_id="cod-automation",
    signing_key=os.getenv("INNGEST_SIGNING_KEY"),
)


@inngest_client.create_function(
    fn_id="cod-wait-and-cancel",
    trigger=TriggerEvent(event="shopify/cod.order.created"),
    retries=2,
)
async def wait_and_cancel(ctx, step):
    order      = ctx.event["data"]
    order_id   = order["order_id"]
    phone      = order["phone"]
    merchant_id = order["merchant_id"]

    # ── Get merchant's configured wait time (default 20 mins) ────────────
    wait_mins = await step.run(
        "get-wait-minutes",
        lambda: _get_wait_minutes(merchant_id)
    )

    # ── Wait ─────────────────────────────────────────────────────────────
    await step.sleep("wait-for-customer-reply", f"{wait_mins}m")

    # ── Check if customer replied during the wait ────────────────────────
    current_status = await step.run(
        "check-order-status",
        lambda: _get_order_status(order_id)
    )

    # Already handled via WhatsApp reply — nothing to do
    if current_status in ("confirmed", "cancelled"):
        return {"status": "already_handled", "via": "whatsapp_reply"}

    # ── No reply — auto cancel ────────────────────────────────────────────
    async def do_cancel():
        await cancel_order(order_id, merchant_id)

    await step.run("auto-cancel-order", do_cancel)

    # Update DB
    def do_db_update():
        _mark_auto_cancelled(order_id)

    await step.run("update-db-cancelled", do_db_update)

    # Notify customer
    async def do_notify():
        await send_message(
            phone,
            f"⚠️ Your order *{order['product']}* has been *automatically cancelled* "
            f"as we didn't receive a confirmation from you.\n\n"
            f"You can place a new order anytime! 🛍️"
        )

    await step.run("notify-customer", do_notify)

    return {"status": "auto_cancelled", "order_id": order_id}


# ── Helpers ───────────────────────────────────────────────────────────────

def _get_wait_minutes(merchant_id: str) -> int:
    supabase = get_supabase()
    result = (
        supabase.table("merchants")
        .select("wait_minutes")
        .eq("merchant_id", merchant_id)
        .execute()
    )
    if result.data:
        return result.data[0].get("wait_minutes", 20)
    return 20


def _get_order_status(order_id: str) -> str | None:
    supabase = get_supabase()
    result = (
        supabase.table("orders")
        .select("status")
        .eq("order_id", order_id)
        .execute()
    )
    return result.data[0]["status"] if result.data else None


def _mark_auto_cancelled(order_id: str):
    supabase = get_supabase()
    supabase.table("orders").update(
        {"status": "auto_cancelled"}
    ).eq("order_id", order_id).execute()
