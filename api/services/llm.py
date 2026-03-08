"""
LLM Reply Parser — Phase 2

Uses Groq + Llama 3 (free tier) to understand customer replies
in any language including English, Urdu (Roman + script), and mixed.

Returns: confirmed / cancelled / unclear
"""

import os
import httpx

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL   = "llama3-8b-8192"

SYSTEM_PROMPT = """
You are analyzing a WhatsApp reply from a customer in Pakistan.
The customer was asked to confirm or cancel a Cash on Delivery order.

Your job is to determine their intent from their reply.

CONFIRMED examples (customer wants the order):
- "yes", "yeah", "yep", "sure", "ok", "okay", "send it", "send"
- "haan", "ha", "ji", "haan ji", "theek hai", "bilkul", "zaroor"
- "bhej do", "bhej dena", "send kar do", "kar do", "chalega"
- "yeah sure send it bro", "haan bhai bhej do", "yes please"
- "kal bhej dena" (send tomorrow), "send karo", "done"
- Any reply showing willingness to receive the order

CANCELLED examples (customer does not want the order):
- "no", "nope", "cancel", "don't want", "dont want"
- "nahi", "na", "nai", "nhi", "nahi chahiye", "cancel kar do"
- "mat bhejo", "wapas", "return", "band kar do"
- Any reply showing they do not want the order

UNCLEAR examples (cannot determine intent):
- Questions: "kitna time lagega", "delivery kab hogi", "price kya hai"
- Greetings: "hello", "hi", "assalam o alaikum"
- Random: "ok bhai", "theek" (without clear order intent)
- Anything that does not clearly confirm or cancel

IMPORTANT:
- "haan bhai bhej do" = confirmed (bhej do means send it)
- "yeah sure send it" = confirmed
- "theek hai" alone = confirmed (it means okay/alright)
- "ji" alone = confirmed (it means yes in Urdu)
- When in doubt between confirmed and unclear → choose confirmed
- Only choose cancelled when customer clearly refuses

Reply with EXACTLY one word: confirmed / cancelled / unclear
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
