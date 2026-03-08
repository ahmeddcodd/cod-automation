"""
COD Automation Flow — Phase 2

Adds smart follow-up reminder before auto-cancelling:
  1. Send WhatsApp confirmation
  2. Wait 15 minutes
  3. Check if replied
  4. If no reply → send Urdu + English reminder
  5. Wait 5 more minutes
  6. Check again
  7. If still no reply → auto cancel
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
    order       = ctx.event["data"]
    order_id    = order["order_id"]
    phone       = order["phone"]
    merchant_id = order["merchant_id"]
    product     = order.get("product", "your order")
    customer    = order.get("customer", "Customer")

    # ── Get merchant wait config ──────────────────────────────────────────
    wait_mins = await step.run(
        "get-wait-minutes",
        lambda: _get_wait_minutes(merchant_id)
    )

    # Split wait into two windows:
    # First wait = 75% of total time → then reminder
    # Second wait = 25% of total time → then cancel
    first_wait  = max(1, int(wait_mins * 0.75))
    second_wait = max(1, wait_mins - first_wait)

    # ── Step 1: Wait first window ─────────────────────────────────────────
    await step.sleep("first-wait", f"{first_wait}m")

    # ── Step 2: Check if already replied ─────────────────────────────────
    status_after_first = await step.run(
        "check-status-after-first-wait",
        lambda: _get_order_status(order_id)
    )

    if status_after_first in ("confirmed", "cancelled"):
        return {"status": "already_handled", "via": "whatsapp_reply"}

    # ── Step 3: Send reminder in English + Urdu ───────────────────────────
    async def send_reminder():
        await send_message(
            phone,
            f"⏰ *Reminder / Yaad Dihani*\n\n"
            f"Aapka order abhi pending hai:\n"
            f"📦 *{product}*\n\n"
            f"Kripya abhi reply karein:\n"
            f"✅ *YES* — Confirm\n"
            f"❌ *NO* — Cancel\n\n"
            f"Your order is still pending. Please reply YES or NO.\n"
            f"⚠️ {second_wait} minute mein automatically cancel ho jayega."
        )

    await step.run("send-reminder", send_reminder)

    # ── Step 4: Wait second window ────────────────────────────────────────
    await step.sleep("second-wait", f"{second_wait}m")

    # ── Step 5: Final status check ────────────────────────────────────────
    final_status = await step.run(
        "final-status-check",
        lambda: _get_order_status(order_id)
    )

    if final_status in ("confirmed", "cancelled"):
        return {"status": "already_handled", "via": "whatsapp_reply_after_reminder"}

    # ── Step 6: Auto cancel ───────────────────────────────────────────────
    async def do_cancel():
        await cancel_order(order_id, merchant_id)

    await step.run("auto-cancel-order", do_cancel)

    def do_db_update():
        _mark_auto_cancelled(order_id)

    await step.run("update-db-cancelled", do_db_update)

    # ── Step 7: Notify customer of cancellation ───────────────────────────
    async def do_notify():
        await send_message(
            phone,
            f"❌ *Order Cancelled / Order Cancel Ho Gaya*\n\n"
            f"Aapka order *{product}* cancel kar diya gaya hai\n"
            f"kyunki humein koi jawab nahi mila.\n\n"
            f"Your order was automatically cancelled as we received no reply.\n\n"
            f"Dobara order karne ke liye hume message karein. 🛍️"
        )

    await step.run("notify-customer", do_notify)

    return {"status": "auto_cancelled", "order_id": order_id}


# ── Helpers ───────────────────────────────────────────────────────────────

def _get_wait_minutes(merchant_id: str) -> int:
    supabase = get_supabase()
    result   = (
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
    result   = (
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
