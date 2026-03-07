import os
import httpx


async def trigger_confirmation_flow(order_data: dict) -> bool:
    event_key = os.getenv("INNGEST_EVENT_KEY")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://inn.gs/e/{event_key}",
            headers={"Content-Type": "application/json"},
            json={
                "name": "shopify/cod.order.created",
                "data": order_data,
            },
            timeout=10,
        )
    return response.status_code == 200
