import os
import httpx


async def trigger_confirmation_flow(order_data: dict) -> bool:
    event_key = str(os.getenv("INNGEST_EVENT_KEY") or "").strip()
    if not event_key:
        print("Inngest trigger skipped: INNGEST_EVENT_KEY is missing")
        return False

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://inn.gs/e/{event_key}",
                headers={"Content-Type": "application/json"},
                json={
                    "name": "shopify/cod.order.created",
                    "data": order_data,
                },
                timeout=5,
            )
        return 200 <= response.status_code < 300
    except Exception as e:
        print(f"Inngest trigger failed: {e}")
        return False
