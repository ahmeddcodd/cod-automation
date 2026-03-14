import httpx
from api.db.supabase import get_supabase

SHOPIFY_API_VERSION = "2024-01"


async def _get_existing_tags(shop: str, token: str, order_id: str) -> str:
    """Fetch the current tags string from a Shopify order."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://{shop}/admin/api/{SHOPIFY_API_VERSION}/orders/{order_id}.json?fields=tags",
                headers={"X-Shopify-Access-Token": token},
                timeout=10,
            )
        if response.status_code == 200:
            return response.json().get("order", {}).get("tags", "")
    except Exception as e:
        print(f"Failed to fetch existing tags: {e}")
    return ""


def _append_tag(existing_tags: str, new_tag: str) -> str:
    """Append a tag to a comma-separated tag string, avoiding duplicates."""
    tags = [t.strip() for t in existing_tags.split(",") if t.strip()]
    if new_tag not in tags:
        tags.append(new_tag)
    return ", ".join(tags)


async def confirm_order(order_id: str, merchant_id: str) -> bool:
    """Tag the order as confirmed on Shopify (appends to existing tags)."""
    merchant = _get_merchant(merchant_id)
    if not merchant:
        return False

    shop  = merchant["shopify_domain"]
    token = merchant["shopify_token"]

    existing_tags = await _get_existing_tags(shop, token, order_id)
    updated_tags  = _append_tag(existing_tags, "cod-confirmed")

    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"https://{shop}/admin/api/{SHOPIFY_API_VERSION}/orders/{order_id}.json",
            headers={
                "X-Shopify-Access-Token": token,
                "Content-Type": "application/json",
            },
            json={"order": {"id": order_id, "tags": updated_tags}},
            timeout=10,
        )
    return response.status_code == 200


async def cancel_order(order_id: str, merchant_id: str) -> bool:
    """Tag the order as cancelled and cancel it on Shopify."""
    merchant = _get_merchant(merchant_id)
    if not merchant:
        return False

    shop  = merchant["shopify_domain"]
    token = merchant["shopify_token"]

    # Tag the order before cancelling
    existing_tags = await _get_existing_tags(shop, token, order_id)
    updated_tags  = _append_tag(existing_tags, "cod-cancelled")

    async with httpx.AsyncClient() as client:
        # Apply tag
        await client.put(
            f"https://{shop}/admin/api/{SHOPIFY_API_VERSION}/orders/{order_id}.json",
            headers={
                "X-Shopify-Access-Token": token,
                "Content-Type": "application/json",
            },
            json={"order": {"id": order_id, "tags": updated_tags}},
            timeout=10,
        )
        # Cancel order
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
