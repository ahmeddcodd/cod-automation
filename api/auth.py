import os
import jwt
import traceback
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

# Supabase JWT Secret from Settings > API > JWT Settings
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    
    if not SUPABASE_JWT_SECRET:
        print("CRITICAL: SUPABASE_JWT_SECRET is not set in environment variables.")
        raise HTTPException(status_code=500, detail="Backend configuration error: SUPABASE_JWT_SECRET is missing")
        
    try:
        # 1. Debug: Inspect the header without verification
        header = jwt.get_unverified_header(token)
        print(f"DEBUG: Token Header: {header}")
        
        # 2. Attempt Decode
        payload = jwt.decode(
            token, 
            SUPABASE_JWT_SECRET, 
            algorithms=["HS256"], 
            options={"verify_aud": False}
        )
        return payload
        
    except jwt.ExpiredSignatureError:
        print("ERROR: Token has expired.")
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
    except jwt.InvalidAlgorithmError as e:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg")
        print(f"ERROR: Algorithm mismatch. Found {alg}, expected HS256. Error: {str(e)}")
        raise HTTPException(status_code=401, detail=f"Algorithm mismatch: {alg}. Expected HS256.")
    except jwt.InvalidTokenError as e:
        print(f"ERROR: Invalid token. {str(e)}")
        # Print the first few characters of the secret for verification (SECURITY: DO NOT LOG THE WHOLE SECRET)
        secret_hint = f"{SUPABASE_JWT_SECRET[:3]}...{SUPABASE_JWT_SECRET[-3:]}" if SUPABASE_JWT_SECRET else "None"
        print(f"DEBUG: Using Secret Hint: {secret_hint}")
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
    except Exception as e:
        print(f"CRITICAL AUTH ERROR: {str(e)}")
        traceback.print_exc()
        raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")
