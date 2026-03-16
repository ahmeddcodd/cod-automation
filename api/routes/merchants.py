import os
import httpx
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from api.db.supabase import get_supabase
from api.auth import get_current_user

META_GRAPH_URL = "https://graph.facebook.com/v22.0"

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
    if config.merchant_id != config.shopify_domain:
        raise HTTPException(
            status_code=400,
            detail="merchant_id must match shopify_domain for webhook routing consistency",
        )

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


class WhatsAppConnect(BaseModel):
    code:            str
    phone_number_id: str
    waba_id:         str


@router.post("/{merchant_id}/whatsapp")
async def connect_whatsapp(
    merchant_id: str,
    body: WhatsAppConnect,
    user: dict = Depends(get_current_user),
):
    """Exchange the Embedded Signup auth code for a token and store WA credentials."""
    supabase = get_supabase()

    # Ownership check
    m_check = supabase.table("merchants").select("user_id").eq("merchant_id", merchant_id).execute()
    if not m_check.data or m_check.data[0].get("user_id") != user.get("sub"):
        raise HTTPException(status_code=403, detail="Unauthorized")

    app_id = os.getenv("META_APP_ID", "")
    app_secret = os.getenv("META_APP_SECRET", "")
    if not app_id or not app_secret:
        raise HTTPException(status_code=500, detail="META_APP_ID or META_APP_SECRET not configured on server")

    # Step 1: Exchange the auth code for a short-lived token
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            token_resp = await client.get(
                f"{META_GRAPH_URL}/oauth/access_token",
                params={
                    "client_id": app_id,
                    "client_secret": app_secret,
                    "redirect_uri": "",  # Embedded Signup uses empty redirect_uri
                    "code": body.code,
                },
            )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to contact Meta: {e}")

    if token_resp.status_code != 200:
        detail = token_resp.text
        print(f"Meta token exchange failed: {token_resp.status_code} {detail}")
        raise HTTPException(status_code=502, detail=f"Meta token exchange failed: {detail}")

    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=502, detail="No access_token in Meta response")

    # Step 2: Exchange short-lived token for a long-lived token (60 days)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            ll_resp = await client.get(
                f"{META_GRAPH_URL}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": app_id,
                    "client_secret": app_secret,
                    "fb_exchange_token": access_token,
                },
            )
        if ll_resp.status_code == 200:
            ll_data = ll_resp.json()
            access_token = ll_data.get("access_token", access_token)
            print(f"Exchanged for long-lived token (expires_in={ll_data.get('expires_in')})")
        else:
            print(f"Long-lived token exchange failed ({ll_resp.status_code}), using short-lived token")
    except Exception as e:
        print(f"Long-lived token exchange error: {e}, using short-lived token")

    # Step 3: Store credentials in the merchants table
    try:
        supabase.table("merchants").update({
            "wa_phone_number_id": body.phone_number_id,
            "wa_waba_id": body.waba_id,
            "wa_access_token": access_token,
        }).eq("merchant_id", merchant_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save WA credentials: {e}")

    return {
        "status": "connected",
        "phone_number_id": body.phone_number_id,
        "waba_id": body.waba_id,
    }


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
