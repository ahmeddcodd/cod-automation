# COD Automation — Phase 1

Automated WhatsApp confirmation for Shopify Cash on Delivery orders.
No AI. No voice calls. Just clean, reliable automation.

---

## How It Works

```
1. Customer places COD order on Shopify
2. Webhook fires → risk check → WhatsApp sent instantly
3. Customer replies YES → order confirmed on Shopify ✅
4. Customer replies NO  → order cancelled on Shopify ❌
5. No reply in 20 mins  → order auto-cancelled ❌
```

---

## Setup (Step by Step)

### 1. Clone & Install
```bash
git clone https://github.com/your-org/cod-automation
cd cod-automation
pip install -r requirements.txt
cp .env.example .env
```

### 2. Supabase
- Create project at supabase.com (free)
- Go to SQL Editor → run `supabase_schema.sql`
- Copy Project URL + Service Role key → paste in `.env` as:
  - `SUPABASE_URL`
  - `SUPABASE_SERVICE_ROLE_KEY`

### 3. Inngest
- Sign up at inngest.com (free — 50k runs/mo)
- Create an app called `cod-automation`
- Copy Event Key + Signing Key → paste in `.env`
- Set webhook URL to: `https://your-domain.vercel.app/api/inngest`

### 4. Meta WhatsApp Cloud API
- Go to developers.facebook.com → create an app → add WhatsApp product
- Set webhook URL: `https://your-domain.vercel.app/api/whatsapp/reply`
- Set verify token to match `META_VERIFY_TOKEN` in your `.env`
- Subscribe to the `messages` webhook field
- Create and approve a template named `_cod_order_confirmation` with 5 body variables:
  1. Customer name
  2. Order name
  3. Product
  4. Quantity
  5. Amount (currency + total)
- Copy Token + Phone Number ID and set template vars in `.env`:
  - `META_ORDER_TEMPLATE_NAME=_cod_order_confirmation`
  - `META_ORDER_TEMPLATE_LANG=en`
  - `META_ORDER_TEMPLATE_LANG_FALLBACKS=en_US,en_GB` (optional retry languages for template lookup)
  - `META_TEMPLATE_FALLBACK_ENABLED=false`
  - `META_FALLBACK_TEMPLATE_NAME=_cod_order_confirmation`
  - If using Vercel, update the same variables in Project Settings and redeploy.

### 5. Shopify Webhook
- In Shopify admin → Settings → Notifications → Webhooks
- Add webhook: Event = `Order creation`, URL = `https://your-domain.vercel.app/api/webhooks/shopify/order`
- Copy the signing secret → paste as `SHOPIFY_WEBHOOK_SECRET` in `.env`

### 6. Register Your Store
```bash
curl -X POST https://your-domain.vercel.app/api/merchants/register \
  -H "Authorization: Bearer <supabase-access-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "<supabase-user-id>",
    "merchant_id": "my-store.myshopify.com",
    "store_name": "My Store",
    "shopify_domain": "my-store.myshopify.com",
    "shopify_token": "shpat_xxxx",
    "wait_minutes": 20
  }'
```
`merchant_id` should match your Shopify shop domain so webhook and merchant records resolve consistently.

### 7. Deploy to Vercel
```bash
npm i -g vercel
vercel login
vercel --prod
```
Add all keys from `.env` in Vercel → Settings → Environment Variables.

---

## Local Development
```bash
uvicorn api.main:app --reload --port 8000
# Expose with ngrok for webhook testing
ngrok http 8000
```

---

## API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/webhooks/shopify/order` | Receives new COD orders from Shopify |
| POST | `/api/whatsapp/reply` | Receives customer WhatsApp replies |
| GET | `/api/whatsapp/reply` | Meta webhook URL verification |
| GET/POST/PUT | `/api/inngest` | Inngest function registration + execution |
| POST | `/api/merchants/register` | Register a Shopify store |
| GET | `/api/merchants/{id}/stats` | View confirmation stats |
| GET | `/api/merchants/{id}/orders` | View all orders |

---

## Order Status Flow

```
pending → confirmed      (customer replied YES)
pending → cancelled      (customer replied NO)
pending → auto_cancelled (no reply after wait_minutes)
```

---

## What's Coming Next

| Phase | What's Added |
|---|---|
| Phase 2 — Lite AI Agent | LLM understands fuzzy replies, ML risk scoring, smart follow-ups |
| Phase 3 — Complete AI Agent | AI voice call if no WhatsApp reply, full autonomous flow |
