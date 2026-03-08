"""
LLM Reply Parser — Phase 2

Uses Groq + Llama 3 (free tier) to understand customer replies
in any language including English, Urdu (Roman + script), and mixed.

Returns: confirmed / cancelled / unclear
"""

import os
import httpx

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL   = "llama3-8b-8192"   # Free on Groq, fast enough for this task

SYSTEM_PROMPT = """
You are analyzing a WhatsApp reply from a customer in Pakistan.
The customer was asked to confirm or cancel a Cash on Delivery order.

Your job is to determine their intent from their reply.

The customer may reply in:
- English: "yes", "no", "sure", "cancel it", "send it"
- Roman Urdu: "haan", "nahi", "theek hai", "cancel kar do", "bhej do", "nai chahiye"
- Urdu script: "ہاں", "نہیں", "ٹھیک ہے"
- Mixed: "yes bhai bhej do", "nahi yaar cancel"
- Indirect: "kal bhej dena" (send tomorrow = confirmed), "abhi nahi" (not now = cancelled)

Rules:
- If the customer clearly wants the order → reply ONLY with: confirmed
- If the customer clearly does not want the order → reply ONLY with: cancelled
- If the reply is a question, greeting, or unclear → reply ONLY with: unclear

Reply with EXACTLY one word: confirmed / cancelled / unclear
Do NOT explain. Do NOT add punctuation. Just the one word.
"""


async def parse_reply_with_llm(customer_reply: str) -> str:
    """
    Use Groq to understand a customer's WhatsApp reply.
    Returns: confirmed / cancelled / unclear
    """
    api_key = os.getenv("GROQ_API_KEY")

    if not api_key:
        print("GROQ_API_KEY missing — falling back to keyword parser")
        return _keyword_fallback(customer_reply)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                GROQ_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":       GROQ_MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": f'Customer reply: "{customer_reply}"'},
                    ],
                    "max_tokens":  5,
                    "temperature": 0,   # deterministic — no creativity needed here
                },
                timeout=8,
            )

        data    = response.json()
        outcome = data["choices"][0]["message"]["content"].strip().lower()
        print(f"LLM parsed '{customer_reply}' → {outcome}")

        # Sanitize — only accept known outcomes
        if "confirm" in outcome:
            return "confirmed"
        elif "cancel" in outcome:
            return "cancelled"
        else:
            return "unclear"

    except Exception as e:
        print(f"Groq LLM failed: {e} — falling back to keyword parser")
        return _keyword_fallback(customer_reply)


def _keyword_fallback(text: str) -> str:
    """
    Fallback keyword parser if Groq is unavailable.
    Handles English + common Roman Urdu keywords.
    """
    cleaned = text.lower().strip()

    CONFIRM_KEYWORDS = [
        # English
        "yes", "confirm", "ok", "okay", "sure", "yep", "yup", "send",
        # Roman Urdu
        "haan", "ha", "ji", "han", "theek", "bilkul", "zaroor", "bhej",
        "bhejdo", "send karo", "kar do", "chalega", "done",
        # Urdu script
        "ہاں", "ٹھیک", "بھیج",
        # Button replies
        "yes ✅", "1",
    ]

    CANCEL_KEYWORDS = [
        # English
        "no", "cancel", "nope", "nah", "stop", "don't", "dont",
        # Roman Urdu
        "nahi", "na", "nai", "band", "mat", "nhi", "nae", "cancel kar",
        "nahi chahiye", "nai chahiye", "wapas", "return",
        # Urdu script
        "نہیں", "مت",
        # Button replies
        "no ❌", "2",
    ]

    if any(kw in cleaned for kw in CONFIRM_KEYWORDS):
        return "confirmed"
    if any(kw in cleaned for kw in CANCEL_KEYWORDS):
        return "cancelled"
    return "unclear"
