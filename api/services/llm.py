"""
LLM Reply Parser — Phase 2

Uses Groq + Llama 3 (free tier) to understand customer replies
in any language including English, Urdu (Roman + script), and mixed.

Returns: confirmed / cancelled / unclear
"""

import os
import json
import httpx

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL   = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """
You are an intent extraction system for a Pakistani e-commerce order confirmation service.

A customer placed a Cash on Delivery order and received a WhatsApp message asking them to confirm or cancel it.
You will receive their reply — which may be a single word, a sentence, or a full conversation.

Your job is to extract their PRIMARY INTENT about the order.

CONFIRMED — customer wants to receive the order:
- Direct: "yes", "ok", "sure", "haan", "ji", "bilkul", "theek hai"
- Action: "bhej do", "send it", "send karo", "deliver kar do"
- Conditional but positive: "haan bhej do lekin jaldi", "yes but change address"
- Delayed but positive: "kal bhej dena", "parso send karna" (send tomorrow = they want it)
- Casual positive: "yeah sure send it bro", "okay okay confirm", "han yar chalega"

CANCELLED — customer does not want the order:
- Direct: "no", "cancel", "nahi", "nai", "nahi chahiye"
- Reason based: "paisa nahi hai", "ghar pe nahi hoon" (not home = likely cancel)
- Indirect: "rehne do", "mat bhejo", "wapas karo", "next time"
- Financial: "abhi afford nahi", "baad mein lena"

UNCLEAR — cannot determine intent:
- Pure questions: "price kya hai", "delivery kab hogi", "kitna time lagega"
- Greetings only: "hello", "hi", "assalam o alaikum" with nothing else
- Truly ambiguous with no order signal

IMPORTANT RULES:
1. If message has BOTH positive and negative signals → pick the STRONGER one
2. If customer mentions changing address/timing but still wants order → CONFIRMED
3. If customer says "not home right now" without saying cancel → UNCLEAR (not cancelled)
4. "kal bhej dena" (send tomorrow) = CONFIRMED
5. "abhi nahi" (not now) = CANCELLED
6. Slang like "yar", "bhai", "bro" are filler words — ignore them for intent
7. When genuinely uncertain between confirmed and unclear → choose CONFIRMED
   (better to confirm and let merchant decide than lose a real order)

Respond ONLY with valid JSON:
{"intent": "confirmed", "reason": "customer said bhej do"}
{"intent": "cancelled", "reason": "customer said nahi chahiye"}
{"intent": "unclear", "reason": "customer asked a question"}
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
                    "max_tokens":  50,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
                timeout=8,
            )

        data    = response.json()
        content = data["choices"][0]["message"]["content"].strip()
        print(f"LLM raw response for '{customer_reply}': {content}")

        parsed = json.loads(content)
        intent = parsed.get("intent", "unclear").lower()
        reason = parsed.get("reason", "")
        print(f"Intent: {intent} | Reason: {reason}")

        if intent == "confirmed":
            return "confirmed"
        elif intent == "cancelled":
            return "cancelled"
        else:
            return "unclear"

    except Exception as e:
        print(f"Groq LLM failed: {e} — falling back to keyword parser")
        return _basic_fallback(customer_reply)


def _basic_fallback(text: str) -> str:
    """
    Minimal fallback ONLY used when Groq is completely unavailable.
    """
    cleaned = text.lower().strip()
    if any(w in cleaned for w in ["yes", "confirm", "ok", "haan", "ji", "bhej", "send"]):
        return "confirmed"
    if any(w in cleaned for w in ["no", "cancel", "nahi", "nai", "mat"]):
        return "cancelled"
    return "unclear"
