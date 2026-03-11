import os
import jwt
from jwt import PyJWKClient
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")

# Fetches the public key directly from Supabase — no key encoding issues
jwks_client = PyJWKClient(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json") if SUPABASE_URL else None


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials

    try:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg", "HS256")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token format.")

    if alg == "ES256":
        if not jwks_client:
            raise HTTPException(status_code=500, detail="Server config error: SUPABASE_URL is missing.")
        try:
            signing_key = jwks_client.get_signing_key_from_jwt(token)
            key = signing_key.key
        except Exception as e:
            print(f"[auth] Failed to fetch signing key: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch signing key: {e}")
    else:
        key = SUPABASE_JWT_SECRET
        if not key:
            raise HTTPException(status_code=500, detail="Server config error: JWT secret missing.")

    try:
        payload = jwt.decode(token, key, algorithms=[alg], options={"verify_aud": False})
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
    except Exception as e:
        print(f"[auth] JWT decode error ({alg}): {e}")
        raise HTTPException(status_code=401, detail=f"Authentication failed: {e}")