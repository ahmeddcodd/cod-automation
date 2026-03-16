import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routes import webhooks, whatsapp, merchants
from api.inngest_server import wire_inngest

app = FastAPI(
    title="COD Automation — Phase 1",
    description="Automated WhatsApp confirmation for Shopify COD orders",
    version="1.0.0",
)


def _cors_origins() -> list[str]:
    raw = str(os.getenv("CORS_ALLOW_ORIGINS") or "*").strip()
    if raw == "*":
        return ["*"]
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    return origins or ["*"]


origins = _cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=origins != ["*"],
)

app.include_router(webhooks.router,  prefix="/api/webhooks",  tags=["Webhooks"])
app.include_router(whatsapp.router,  prefix="/api/whatsapp",  tags=["WhatsApp"])
app.include_router(merchants.router, prefix="/api/merchants", tags=["Merchants"])
wire_inngest(app)

@app.get("/")
async def root():
    return {"status": "COD Automation Phase 1 is live 🚀"}
