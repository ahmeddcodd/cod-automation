import os
import httpx


async def trigger_confirmation_flow(order_data: dict) -> bool:
    event_key = os.getenv("INNGEST_EVENT_KEY")

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
        return response.status_code == 200
    except Exception as e:
        print(f"Inngest trigger failed: {e}")
        return False
