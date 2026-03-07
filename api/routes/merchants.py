from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from api.db.supabase import get_supabase

router = APIRouter()


class MerchantConfig(BaseModel):
    merchant_id:     str
    store_name:      str
    shopify_domain:  str
    shopify_token:   str
    wait_minutes:    int   = 20    # How long to wait before auto-cancelling


@router.post("/register")
async def register_merchant(config: MerchantConfig):
    supabase = get_supabase()
    supabase.table("merchants").upsert(config.dict()).execute()
    return {"status": "registered", "merchant_id": config.merchant_id}


@router.get("/{merchant_id}")
async def get_merchant(merchant_id: str):
    supabase = get_supabase()
    result = supabase.table("merchants").select("*").eq("merchant_id", merchant_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Merchant not found")
    return result.data[0]


@router.get("/{merchant_id}/orders")
async def get_orders(merchant_id: str, limit: int = 50):
    supabase = get_supabase()
    orders = (
        supabase.table("orders")
        .select("*")
        .eq("merchant_id", merchant_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    ).data
    return {"orders": orders, "total": len(orders)}


@router.get("/{merchant_id}/stats")
async def get_stats(merchant_id: str):
    supabase = get_supabase()
    orders = supabase.table("orders").select("status").eq("merchant_id", merchant_id).execute().data

    total          = len(orders)
    confirmed      = sum(1 for o in orders if o["status"] == "confirmed")
    cancelled      = sum(1 for o in orders if o["status"] == "cancelled")
    auto_cancelled = sum(1 for o in orders if o["status"] == "auto_cancelled")
    pending        = sum(1 for o in orders if o["status"] == "pending")

    return {
        "total":             total,
        "confirmed":         confirmed,
        "cancelled":         cancelled,
        "auto_cancelled":    auto_cancelled,
        "pending":           pending,
        "confirmation_rate": f"{(confirmed / total * 100):.1f}%" if total else "0%",
        "fake_order_rate":   f"{((cancelled + auto_cancelled) / total * 100):.1f}%" if total else "0%",
    }
