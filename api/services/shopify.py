import httpx
from api.db.supabase import get_supabase

SHOPIFY_API_VERSION = "2024-01"


async def confirm_order(order_id: str, merchant_id: str) -> bool:
    """Tag the order as confirmed on Shopify."""
    merchant = _get_merchant(merchant_id)
    if not merchant:
        return False

    shop  = merchant["shopify_domain"]
    token = merchant["shopify_token"]

    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"https://{shop}/admin/api/{SHOPIFY_API_VERSION}/orders/{order_id}.json",
            headers={
                "X-Shopify-Access-Token": token,
                "Content-Type": "application/json",
            },
            json={"order": {"id": order_id, "tags": "cod-confirmed"}},
            timeout=10,
        )
    return response.status_code == 200


async def cancel_order(order_id: str, merchant_id: str) -> bool:
    """Cancel the order on Shopify."""
    merchant = _get_merchant(merchant_id)
    if not merchant:
        return False

    shop  = merchant["shopify_domain"]
    token = merchant["shopify_token"]

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://{shop}/admin/api/{SHOPIFY_API_VERSION}/orders/{order_id}/cancel.json",
            headers={
                "X-Shopify-Access-Token": token,
                "Content-Type": "application/json",
            },
            json={"reason": "customer", "email": False},
            timeout=10,
        )
    return response.status_code == 200


def _get_merchant(merchant_id: str) -> dict | None:
    supabase = get_supabase()
    result = (
        supabase.table("merchants")
        .select("shopify_domain, shopify_token")
        .eq("merchant_id", merchant_id)
        .execute()
    )
    return result.data[0] if result.data else None
