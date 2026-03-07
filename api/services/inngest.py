import os
import httpx


async def trigger_confirmation_flow(order_data: dict) -> bool:
    """Fire an event to Inngest to start the wait → auto-cancel flow."""
    event_key = os.getenv("INNGEST_EVENT_KEY")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://inn.gs/e/",
            headers={
                "Authorization": f"Bearer {event_key}",
                "Content-Type":  "application/json",
            },
            json={
                "name": "shopify/cod.order.created",
                "data": order_data,
            },
            timeout=10,
        )
    return response.status_code == 200
