import os
import jwt
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

# Supabase JWT Secret from Settings > API > JWT Settings
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    if not SUPABASE_JWT_SECRET:
        raise HTTPException(status_code=500, detail="Backend configuration error: SUPABASE_JWT_SECRET is missing")
        
    try:
        # PyJWT is more robust with algorithms
        payload = jwt.decode(
            token, 
            SUPABASE_JWT_SECRET, 
            algorithms=["HS256"], 
            options={"verify_aud": False}
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
    except jwt.InvalidAlgorithmError:
        # Let's try to get more detail if it's specifically an algorithm mismatch
        try:
            header = jwt.get_unverified_header(token)
            alg = header.get("alg")
            raise HTTPException(status_code=401, detail=f"Invalid algorithm: {alg}. Expected HS256.")
        except:
            raise HTTPException(status_code=401, detail="Invalid encryption algorithm.")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")
