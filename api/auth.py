import os
import jwt
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

# For ES256, Supabase provides a Public Key (starts with -----BEGIN PUBLIC KEY-----)
# For HS256, Supabase provides a JWT Secret (usually a long string or UUID)
SUPABASE_JWT_PUBLIC_KEY = os.getenv("SUPABASE_JWT_PUBLIC_KEY")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")

import re

def prepare_public_key(key_str: str) -> str:
    """Robustly formats a PEM public key for the cryptography library."""
    if not key_str:
        return key_str
    
    # 1. Clean up common formatting issues
    # Handle literal '\n' strings often found in env vars
    cleaned = key_str.replace("\\n", "\n").replace("\\r", "\n").strip()
    
    # 2. Extract the base64 content
    # Look for content between ANY headers or just take the whole thing
    match = re.search(r"-----BEGIN.*?-----(.*?)-----END.*?-----", cleaned, re.DOTALL)
    if match:
        content = match.group(1)
    else:
        content = cleaned
        
    # 3. Remove all non-base64 characters (whitespace, newlines, etc.)
    # This leaves only the actual key data
    content = "".join(re.findall(r"[A-Za-z0-9+/=]+", content))
    
    if not content:
        return key_str # Fallback to original if we somehow wiped it all
        
    # 4. Reconstruct the PEM with EXACTLY 64 characters per line
    # This is what 'MalformedFraming' usually complains about
    lines = [content[i:i+64] for i in range(0, len(content), 64)]
    return "-----BEGIN PUBLIC KEY-----\n" + "\n".join(lines) + "\n-----END PUBLIC KEY-----"

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    
    # 1. Inspect header to see which algorithm the token is using
    try:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg", "HS256")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token format")

    # 2. Select the correct key based on the algorithm
    if alg == "ES256":
        key = prepare_public_key(SUPABASE_JWT_PUBLIC_KEY)
        if not key:
            raise HTTPException(
                status_code=500, 
                detail="Server configuration error: ES256 Public Key is missing."
            )
    else:
        key = SUPABASE_JWT_SECRET
        if not key:
            raise HTTPException(
                status_code=500, 
                detail="Server configuration error: JWT Secret is missing."
            )
        
    try:
        # 3. Decode and verify the token
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
        error_msg = str(e)
        print(f"JWT Decode Error ({alg}): {error_msg}")
        
        # Specific hint for the 'MalformedFraming' error
        if "MalformedFraming" in error_msg:
            detail = "Authentication failed: The Public Key format is incorrect. Please ensure you copied the PUBLIC key from Supabase (starts with -----BEGIN PUBLIC KEY-----)."
        elif "key" in error_msg.lower():
            detail = f"Authentication failed: Key mismatch. Your SUPABASE_JWT_PUBLIC_KEY might be the wrong type for {alg}."
        else:
            detail = f"Authentication failed: {error_msg}"
            
        raise HTTPException(status_code=401, detail=detail)
