"""
LLM Reply Parser — Phase 2

Uses Groq + Llama 3 (free tier) to understand customer replies
in any language including English, Urdu (Roman + script), and mixed.

Returns: confirmed / cancelled / unclear
"""

import os
import httpx

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL   = "llama-3.1-70b-versatile"

SYSTEM_PROMPT = """
You are classifying customer intent from a WhatsApp message.
The customer was asked: "Do you want to confirm or cancel your Cash on Delivery order?"

Classify their reply as:
- confirmed: if they want the order (yes, sure, send it, haan, ji, bhej do, yeah)
- cancelled: if they don't want it (no, cancel, nahi, nai, mat bhejo)
- unclear: if you cannot tell

Reply with ONE word only: confirmed / cancelled / unclear
"""


async def parse_reply_with_llm(customer_reply: str) -> str:
    """
    Use Groq to understand a customer's WhatsApp reply.
    Returns: confirmed / cancelled / unclear
    """
    api_key = os.getenv("GROQ_API_KEY")

    if not api_key:
        print("GROQ_API_KEY missing — falling back to basic keyword parser")
        return _basic_fallback(customer_reply)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                GROQ_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":    GROQ_MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": f'Customer reply: "{customer_reply}"'},
                    ],
                    "max_tokens":  5,
                    "temperature": 0,
                },
                timeout=8,
            )

        data    = response.json()
        outcome = data["choices"][0]["message"]["content"].strip().lower()
        print(f"LLM parsed '{customer_reply}' → {outcome}")

        if "confirm" in outcome:
            return "confirmed"
        elif "cancel" in outcome:
            return "cancelled"
        else:
            return "unclear"

    except Exception as e:
        print(f"Groq LLM failed: {e} — falling back to basic keyword parser")
        return _basic_fallback(customer_reply)


def _basic_fallback(text: str) -> str:
    """
    Minimal fallback ONLY used when Groq is completely unavailable.
    Just the most universal signals — LLM handles everything else.
    """
    cleaned = text.lower().strip()

    if any(w in cleaned for w in ["yes", "confirm", "ok", "haan", "ji", "1"]):
        return "confirmed"
    if any(w in cleaned for w in ["no", "cancel", "nahi", "nai", "2"]):
        return "cancelled"
    return "unclear"
