-- ── Merchants ────────────────────────────────────────────────────────────
CREATE TABLE merchants (
    id               UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    merchant_id      TEXT UNIQUE NOT NULL,
    store_name       TEXT NOT NULL,
    shopify_domain   TEXT NOT NULL,       -- e.g. your-store.myshopify.com
    shopify_token    TEXT NOT NULL,       -- Admin API access token
    wait_minutes     INT DEFAULT 20,      -- How long to wait before auto-cancel
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

-- ── Orders ────────────────────────────────────────────────────────────────
CREATE TABLE orders (
    id               UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    order_id         TEXT UNIQUE NOT NULL,
    order_name       TEXT,                -- e.g. #1042
    merchant_id      TEXT REFERENCES merchants(merchant_id),
    phone            TEXT NOT NULL,
    customer         TEXT,
    product          TEXT,
    amount           TEXT,
    currency         TEXT DEFAULT 'PKR',
    risk_score       FLOAT DEFAULT 0.0,
    risk_flags       TEXT[],              -- Array of flag strings
    status           TEXT DEFAULT 'pending',
    -- pending | confirmed | cancelled | auto_cancelled
    reply            TEXT,               -- Raw text of customer's reply
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

-- ── Indexes ───────────────────────────────────────────────────────────────
CREATE INDEX idx_orders_phone      ON orders(phone);
CREATE INDEX idx_orders_merchant   ON orders(merchant_id);
CREATE INDEX idx_orders_status     ON orders(status);
CREATE INDEX idx_orders_created_at ON orders(created_at DESC);

-- ── Auto-update updated_at on row change ──────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER orders_set_updated_at
BEFORE UPDATE ON orders
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
