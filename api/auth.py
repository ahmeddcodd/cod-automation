import os
import jwt
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

# For ES256, Supabase provides a Public Key (starts with -----BEGIN PUBLIC KEY-----)
# For HS256, Supabase provides a JWT Secret (usually a long string or UUID)
SUPABASE_JWT_PUBLIC_KEY = os.getenv("SUPABASE_JWT_PUBLIC_KEY")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    
    # 1. Inspect header to see which algorithm the token is using
    try:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg", "HS256")
        print(f"DEBUG: Validating token with algorithm: {alg}")
    except Exception as e:
        print(f"DEBUG: Failed to read token header: {str(e)}")
        raise HTTPException(status_code=401, detail="Invalid token format")

    # 2. Select the correct key based on the algorithm
    if alg == "ES256":
        key = SUPABASE_JWT_PUBLIC_KEY
        if not key:
            print("ERROR: Token is ES256 but SUPABASE_JWT_PUBLIC_KEY is not set in environment.")
            raise HTTPException(
                status_code=500, 
                detail="Server configuration error: ES256 Public Key is missing. Please add SUPABASE_JWT_PUBLIC_KEY to your environment variables."
            )
    else:
        # Default to HS256 for other algorithms (like HS384, HS512) or explicit HS256
        key = SUPABASE_JWT_SECRET
        if not key:
            print(f"ERROR: Token is {alg} but SUPABASE_JWT_SECRET is not set in environment.")
            raise HTTPException(
                status_code=500, 
                detail="Server configuration error: JWT Secret is missing. Please add SUPABASE_JWT_SECRET to your environment variables."
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
    except jwt.InvalidAlgorithmError:
        print(f"ERROR: Algorithm {alg} is not allowed or supported.")
        raise HTTPException(status_code=401, detail=f"Invalid encryption algorithm: {alg}")
    except Exception as e:
        print(f"JWT Decode Error ({alg}): {str(e)}")
        # If it's a key format error, give a hint
        if "public key" in str(e).lower() or "key" in str(e).lower():
            detail = f"Authentication failed: Key mismatch or invalid format for {alg}. Check your environment variables."
        else:
            detail = f"Authentication failed: {str(e)}"
        raise HTTPException(status_code=401, detail=detail)
