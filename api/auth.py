import os
import jwt
import re
import base64
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from cryptography.hazmat.primitives.serialization import load_der_public_key

security = HTTPBearer()

SUPABASE_JWT_PUBLIC_KEY = os.getenv("SUPABASE_JWT_PUBLIC_KEY")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")

def load_ec_public_key(key_str: str):
    if not key_str:
        return None

    cleaned = key_str.strip()

    # If it's a full PEM string, extract just the base64 content
    match = re.search(r"-----BEGIN.*?-----(.*?)-----END.*?-----", cleaned, re.DOTALL)
    if match:
        cleaned = "".join(re.findall(r"[A-Za-z0-9+/=]+", match.group(1)))

    # Vercel sometimes turns '+' into ' ' — restore it
    cleaned = cleaned.replace(" ", "+")

    try:
        der_bytes = base64.b64decode(cleaned)
        key = load_der_public_key(der_bytes)
        print("[auth] EC public key loaded successfully.")
        return key
    except Exception as e:
        print(f"[auth] FAILED TO LOAD EC PUBLIC KEY: {e}")
        print(f"[auth] Key string (sanitized): {cleaned[:40]}...")
        return None

_ec_public_key = load_ec_public_key(SUPABASE_JWT_PUBLIC_KEY)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials

    try:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg", "HS256")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token format.")

    if alg == "ES256":
        if not _ec_public_key:
            raise HTTPException(status_code=500, detail="Server config error: EC public key missing or invalid.")
        key = _ec_public_key
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