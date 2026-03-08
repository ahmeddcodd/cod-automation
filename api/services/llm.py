GROQ_MODEL = "llama3-70b-8192"

SYSTEM_PROMPT = """
You are a sentiment classifier for WhatsApp order confirmation messages.

A customer in Pakistan received a message asking them to confirm or cancel a Cash on Delivery order.
Classify their reply into exactly one of these three categories:

confirmed  → customer wants to receive the order (positive intent)
cancelled  → customer does not want the order (negative intent)
unclear    → cannot determine intent (question, greeting, gibberish)

The customer may write in English, Urdu, Roman Urdu, or mixed.
Positive words include: yes, ok, sure, haan, ji, theek, bhej, send, bilkul, yeah, yep, alright, fine, cool, sounds good
Negative words include: no, cancel, nahi, nai, mat, band, nahi chahiye, don't, nope, never

If the message contains ANY positive word alongside "send", "it", "bro", "yar", "please" → it is confirmed.

Respond with EXACTLY one word only: confirmed / cancelled / unclear
"""


def _normalize_slang(text: str) -> str:
    """
    Normalize casual English slang to cleaner phrases
    before sending to LLM.
    """
    replacements = {
        "yeah sure":     "yes",
        "yeah":          "yes",
        "yep":           "yes",
        "yup":           "yes",
        "send it":       "confirm",
        "send it bro":   "confirm",
        "send it yar":   "confirm",
        "go ahead":      "confirm",
        "sounds good":   "confirm",
        "alright":       "yes",
        "fine":          "yes",
        "cool":          "yes",
        "dont want":     "cancel",
        "don't want":    "cancel",
        "not interested": "cancel",
        "forget it":     "cancel",
        "never mind":    "cancel",
    }

    cleaned = text.lower().strip()
    for slang, normalized in replacements.items():
        if slang in cleaned:
            cleaned = cleaned.replace(slang, normalized)
    return cleaned


async def parse_reply_with_llm(customer_reply: str) -> str:
    api_key = os.getenv("GROQ_API_KEY")

    # Normalize slang first
    normalized = _normalize_slang(customer_reply)
    if normalized != customer_reply.lower():
        print(f"Normalized '{customer_reply}' → '{normalized}'")

    if not api_key:
        print("GROQ_API_KEY missing — falling back to basic keyword parser")
        return _basic_fallback(normalized)

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
                        {"role": "user",   "content": f'Customer reply: "{normalized}"'},
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
        return _basic_fallback(normalized)


def _basic_fallback(text: str) -> str:
    cleaned = text.lower().strip()
    if any(w in cleaned for w in ["yes", "confirm", "ok", "haan", "ji", "1"]):
        return "confirmed"
    if any(w in cleaned for w in ["no", "cancel", "nahi", "nai", "2"]):
        return "cancelled"
    return "unclear"
