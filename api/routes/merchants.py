from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from api.db.supabase import get_supabase
from api.auth import get_current_user

router = APIRouter()


class MerchantConfig(BaseModel):
    user_id:         str
    merchant_id:     str
    store_name:      str
    shopify_domain:  str
    shopify_token:   str
    wait_minutes:    int   = 20


@router.post("/register")
async def register_merchant(config: MerchantConfig, user: dict = Depends(get_current_user)):
    # Verify that the user_id in the payload matches the user_id in the JWT
    if config.user_id != user.get("sub"):
        raise HTTPException(status_code=403, detail="Unauthorized: User ID mismatch")

    supabase = get_supabase()
    try:
        supabase.table("merchants").upsert(config.dict()).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save merchant: {exc}") from exc

    result = (
        supabase.table("merchants")
        .select("*")
        .eq("merchant_id", config.merchant_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=500, detail="Merchant not found after save")
    return {"status": "registered", "merchant": result.data[0]}


@router.get("/me")
async def get_my_merchant(user: dict = Depends(get_current_user)):
    user_id = user.get("sub")
    supabase = get_supabase()
    
    result = supabase.table("merchants").select("*").eq("user_id", user_id).limit(1).execute()
    
    if not result.data:
        raise HTTPException(status_code=404, detail="No merchant found for this user.")
    
    return result.data[0]

@router.get("/{merchant_id}")
async def get_merchant(merchant_id: str, user: dict = Depends(get_current_user)):
    supabase = get_supabase()
    result = supabase.table("merchants").select("*").eq("merchant_id", merchant_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Merchant not found")
    
    # Ownership check
    if result.data[0].get("user_id") != user.get("sub"):
        raise HTTPException(status_code=403, detail="Unauthorized access to this merchant")
        
    return result.data[0]


@router.get("/{merchant_id}/orders")
async def get_orders(merchant_id: str, limit: int = 50, user: dict = Depends(get_current_user)):
    supabase = get_supabase()
    
    # First, verify ownership of the merchant
    m_check = supabase.table("merchants").select("user_id").eq("merchant_id", merchant_id).execute()
    if not m_check.data or m_check.data[0].get("user_id") != user.get("sub"):
        raise HTTPException(status_code=403, detail="Unauthorized")

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
async def get_stats(merchant_id: str, user: dict = Depends(get_current_user)):
    supabase = get_supabase()
    
    # Ownership check
    m_check = supabase.table("merchants").select("user_id").eq("merchant_id", merchant_id).execute()
    if not m_check.data or m_check.data[0].get("user_id") != user.get("sub"):
        raise HTTPException(status_code=403, detail="Unauthorized")

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
