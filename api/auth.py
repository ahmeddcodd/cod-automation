import os
import jwt
import re
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from cryptography.hazmat.primitives.serialization import load_pem_public_key

security = HTTPBearer()

SUPABASE_JWT_PUBLIC_KEY = os.getenv("SUPABASE_JWT_PUBLIC_KEY")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")

def prepare_ec_key(key_str: str):
    """Formats and loads a PEM public key for ES256 verification."""
    if not key_str:
        return None

    # 1. Clean common formatting issues from env vars
    cleaned = key_str.replace("\\n", "\n").replace("\\r", "\n").strip()

    # 2. Extract base64 content only
    match = re.search(r"-----BEGIN.*?-----(.*?)-----END.*?-----", cleaned, re.DOTALL)
    if match:
        content = match.group(1)
    else:
        content = cleaned

    # 3. Clean all non-base64 characters
    content = "".join(re.findall(r"[A-Za-z0-9+/=]+", content))

    if not content:
        return None

    # 4. Reconstruct clean PEM
    lines = [content[i:i+64] for i in range(0, len(content), 64)]
    pem = "-----BEGIN PUBLIC KEY-----\n" + "\n".join(lines) + "\n-----END PUBLIC KEY-----"

    return pem

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials

    # Get fresh env vars
    public_key_raw = os.getenv("SUPABASE_JWT_PUBLIC_KEY")
    jwt_secret = os.getenv("SUPABASE_JWT_SECRET")

    try:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg", "HS256")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token format")

    if alg == "ES256":
        if not public_key_raw:
            print("ERROR: SUPABASE_JWT_PUBLIC_KEY is not set in environment.")
            raise HTTPException(status_code=500, detail="Server config error: ES256 Public Key is missing.")

        key = prepare_ec_key(public_key_raw)
        if not key:
            print("ERROR: SUPABASE_JWT_PUBLIC_KEY exists but is empty after cleaning.")
            raise HTTPException(status_code=500, detail="Server config error: ES256 Public Key is invalid.")
    else:
        key = jwt_secret
        if not key:
            raise HTTPException(status_code=500, detail="Server config error: JWT Secret is missing.")

    try:
        # Decode and verify
        payload = jwt.decode(
            token, 
            key, 
            algorithms=[alg], 
            options={"verify_aud": False}
        )
        return payload

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
    except Exception as e:
        print(f"JWT Decode Error ({alg}): {str(e)}")
        raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")