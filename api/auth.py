import os
import jwt
import re
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from cryptography.hazmat.primitives.serialization import load_pem_public_key

security = HTTPBearer()

SUPABASE_JWT_PUBLIC_KEY = os.getenv("SUPABASE_JWT_PUBLIC_KEY")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")

def load_ec_public_key(key_str: str):
    """
    Accepts either:
      - Raw base64 DER (no headers) — what Supabase often gives you
      - A full PEM string (with -----BEGIN PUBLIC KEY----- headers)
    Returns a cryptography EllipticCurvePublicKey object.
    """
    if not key_str:
        return None

    cleaned = key_str.replace("\\n", "\n").replace("\\r", "\n").strip()

    # If there are no PEM headers, it's a raw base64 DER blob — wrap it
    if "-----BEGIN" not in cleaned:
        # Strip any stray whitespace in the base64
        b64 = "".join(re.findall(r"[A-Za-z0-9+/=]+", cleaned))
        lines = [b64[i:i+64] for i in range(0, len(b64), 64)]
        cleaned = "-----BEGIN PUBLIC KEY-----\n" + "\n".join(lines) + "\n-----END PUBLIC KEY-----"

    try:
        return load_pem_public_key(cleaned.encode("utf-8"))
    except Exception as e:
        print(f"[auth] Failed to load EC public key: {e}")
        raise ValueError(f"Invalid EC public key: {e}")


# Pre-load the key at startup so errors surface immediately
_ec_public_key = None
if SUPABASE_JWT_PUBLIC_KEY:
    try:
        _ec_public_key = load_ec_public_key(SUPABASE_JWT_PUBLIC_KEY)
        print("[auth] EC public key loaded successfully.")
    except Exception as e:
        print(f"[auth] WARNING: Could not pre-load EC public key: {e}")


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