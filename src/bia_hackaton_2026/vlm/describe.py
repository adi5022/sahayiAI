"""Image understanding via Groq vision (qwen/qwen3.8-27b) and merging with the user's text."""
import base64
import os
import time

from groq import Groq

from bia_hackaton_2026.llm import usage

VLM_MODEL = "qwen/qwen3.8-27b"
MAX_IMAGE_BYTES = 4 * 1024 * 1024  # Groq limit for base64-encoded images in a request

SYSTEM_PROMPT = (
    "You describe images for a caregiver-assistance system used by senior citizens and persons with "
    "disabilities. Describe only what is visibly present: people, posture, visible injuries, objects, "
    "hazards, surroundings. For a screenshot or photo of a message, call log or website, transcribe the sender name or number, the full message, "
    "any links and any amounts or account details exactly. Transcribe any readable text exactly (Malayalam or English), e.g. medicine "
    "labels, prescriptions, screens. Do NOT diagnose, name medical conditions, or recommend medication. "
    "If something is unclear, say it is unclear. Be concise: at most 6 short bullet points."
)


def describe_image(image_bytes: bytes, mime: str = "image/jpeg", user_text: str = "", groq_key: str | None = None) -> dict:
    """Returns dict: description, model, seconds, error."""
    groq_key = groq_key or os.environ.get("GROQ_API_KEY")
    if not groq_key:
        return {"description": "", "model": VLM_MODEL, "seconds": 0.0, "error": "Groq API key missing."}
    if len(image_bytes) > MAX_IMAGE_BYTES:
        return {"description": "", "model": VLM_MODEL, "seconds": 0.0,
                "error": f"Image too large ({len(image_bytes) // 1024} KB). Limit is 4 MB."}
    b64 = base64.b64encode(image_bytes).decode()
    question = f"User's message: {user_text}\n\nDescribe the image." if user_text.strip() else "Describe the image."
    t0 = time.perf_counter()
    try:
        r = Groq(api_key=groq_key.strip()).chat.completions.create(
            model=VLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ]},
            ],
            temperature=0.2,
            max_completion_tokens=600,
        )
        text = (r.choices[0].message.content or "").strip()
        u = getattr(r, "usage", None)
        usage.record("groq", VLM_MODEL, "vision", bool(text), 200, getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0), time.perf_counter() - t0)
        return {"description": text, "model": VLM_MODEL, "seconds": round(time.perf_counter() - t0, 2),
                "error": "" if text else "Model returned an empty description."}
    except Exception as e:
        return {"description": "", "model": VLM_MODEL, "seconds": round(time.perf_counter() - t0, 2),
                "error": f"Groq vision failed: {e}"}


def build_agent_input(user_text: str, image_description: str = "") -> str:
    """Combine the user's text (transcript) and image description into one message for the LLM agent.
    Keeps the two sources labelled so the agent can tell what the user said from what was seen."""
    parts = []
    if user_text.strip():
        parts.append(f"[USER MESSAGE]\n{user_text.strip()}")
    if image_description.strip():
        parts.append(f"[IMAGE DESCRIPTION (auto-generated, may be inaccurate)]\n{image_description.strip()}")
    return "\n\n".join(parts)
