"""Multi-agent image check: two independent VLMs describe the image, a judge compares them against
the image and returns one final description. Goal: avoid a wrong or missing observation reaching the agent.

  Agent A: Groq qwen/qwen3.8-27b          (describe_image)
  Agent B: Gemini (default gemini-3.5-flash-lite)
  Judge:   Groq qwen (fast, sees the image and both descriptions); Gemini if Groq fails
"""
import base64
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from groq import Groq
from google import genai
from google.genai import types

from bia_hackaton_2026.llm import usage
from bia_hackaton_2026.llm.groq_chat import key_tag, with_groq_keys
from bia_hackaton_2026.vlm.describe import SYSTEM_PROMPT, VLM_MODEL, describe_image

# flash-lite: consistently 2-3 s. gemini-3.5-flash was 5-31 s on the free tier (it "thinks" ~400 tokens).
GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_FALLBACKS = ("gemini-3.5-flash",)  # tried if the main model errors / is unavailable
GEMINI_TIMEOUT_MS = 10000

JUDGE_PROMPT = """You are a careful reviewer for a caregiver-assistance system for elderly people. \
You are given an image and two independent descriptions of it (A and B) written by different AI models. \
Your job is to produce the single most accurate description and to prevent misdiagnosis or missed danger.

Method:
1. Look at the image yourself. Check every claim in A and B against what is actually visible.
2. List the discrepancies: things one description states that the other omits or contradicts.
3. Decide for each discrepancy whether the image supports it.
4. Write the final description from facts the image supports.

Safety rules:
- Never drop an injury, bleeding, fall, hazard or medicine-label observation reported by either description unless the image clearly shows it is absent. \
If you cannot tell, keep it and mark it "possibly" / "unclear". A missed danger is worse than a cautious extra note.
- Describe only what is visible. No diagnosis, no condition names, no medication advice.
- Transcribe readable text exactly (Malayalam or English). If text is unreadable, say so.
- At most 6 short bullet points in the final description.

winner: "A" or "B" if one description is clearly more accurate and complete and you used it nearly as is; otherwise "merged".
agreement: "high" if A and B agree on all safety-relevant facts, "partial" if they differ on minor details, "low" if they differ on injuries, \
severity, hazards or readable text.
needs_human_review: true if agreement is "low" or the image is too unclear to describe the safety-relevant parts reliably.

Reply with ONLY this JSON:
{"winner": "A|B|merged", "agreement": "high|partial|low", "needs_human_review": true|false,
 "discrepancies": ["<short item>", ...], "reasoning": "<one or two sentences>", "description": "<final bullet-point description>"}"""


def _gemini_client(api_key: str | None):
    key = (api_key or os.environ.get("GEMINI_API_KEY") or "").strip()
    return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS)) if key else None


def _gemini_call(client, contents, system: str, json_mode: bool = False, models=None) -> tuple[str, str]:
    """Try the main Gemini model, then fallbacks. Returns (text, model_used). Raises on total failure."""
    last = None
    for model in models or (GEMINI_MODEL, *GEMINI_FALLBACKS):
        try:
            cfg = types.GenerateContentConfig(
                system_instruction=system, temperature=0.2,
                response_mime_type="application/json" if json_mode else None,
            )
            _t0 = time.perf_counter()
            r = client.models.generate_content(model=model, contents=contents, config=cfg)
            _u = getattr(r, "usage_metadata", None)
            usage.record("gemini", model, "vision", bool(r.text and r.text.strip()), 200, getattr(_u, "prompt_token_count", 0) or 0,
                         getattr(_u, "candidates_token_count", 0) or 0, time.perf_counter() - _t0)
            if r.text and r.text.strip():
                return r.text.strip(), model
            last = RuntimeError("empty response")
        except Exception as e:  # 503 high demand, quota, etc.
            usage.record("gemini", model, "vision", False, getattr(e, "code", 0) or 0)
            last = e
    raise last or RuntimeError("Gemini unavailable")


def _judge_groq(image_bytes: bytes, mime: str, user_text: str, groq_key: str | None) -> tuple[str, str]:
    """Judge on Groq (fast, sees the image). Returns (json_text, model)."""
    key = (groq_key or os.environ.get("GROQ_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("Groq API key missing")
    b64 = base64.b64encode(image_bytes).decode()
    r = with_groq_keys(lambda client: client.chat.completions.create(
        model=VLM_MODEL,
        messages=[
            {"role": "system", "content": JUDGE_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]},
        ],
        response_format={"type": "json_object"}, temperature=0.1, max_completion_tokens=900,
    ), key, "vision", 20.0, VLM_MODEL)
    _u = getattr(r, "usage", None)
    usage.record("groq", VLM_MODEL + key_tag(), "vision", True, 200, getattr(_u, "prompt_tokens", 0), getattr(_u, "completion_tokens", 0))
    return (r.choices[0].message.content or "").strip(), VLM_MODEL


def describe_image_gemini(image_bytes: bytes, mime: str = "image/jpeg", gemini_key: str | None = None) -> dict:
    """Agent B. Returns dict: description, model, seconds, error."""
    client = _gemini_client(gemini_key)
    if client is None:
        return {"description": "", "model": GEMINI_MODEL, "seconds": 0.0, "error": "Gemini API key missing."}
    t0 = time.perf_counter()
    try:
        text, model = _gemini_call(
            client,
            [types.Part.from_bytes(data=image_bytes, mime_type=mime), "Describe the image."],
            SYSTEM_PROMPT,
        )
        return {"description": text, "model": model, "seconds": round(time.perf_counter() - t0, 2), "error": ""}
    except Exception as e:
        return {"description": "", "model": GEMINI_MODEL, "seconds": round(time.perf_counter() - t0, 2),
                "error": f"Gemini vision failed: {str(e)[:200]}"}


def describe_verified(image_bytes: bytes, mime: str = "image/jpeg", groq_key: str | None = None,
                      gemini_key: str | None = None) -> dict:
    """Run both VLMs in parallel, then the judge. Always returns a dict with:
    description (final), status ('verified' | 'single_model' | 'failed'), agreement, winner, needs_human_review,
    discrepancies, reasoning, a / b (raw agent results), judge_model, seconds, error."""
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as ex:
        fa = ex.submit(describe_image, image_bytes, mime, "", groq_key)
        fb = ex.submit(describe_image_gemini, image_bytes, mime, gemini_key)
        a, b = fa.result(), fb.result()

    out = {"a": a, "b": b, "winner": "", "agreement": "", "needs_human_review": False, "discrepancies": [],
           "reasoning": "", "judge_model": "", "description": "", "status": "failed", "error": ""}

    if a["description"] and b["description"]:
        judge_input = (f"Description A (Groq {a['model']}):\n{a['description']}\n\n"
                       f"Description B (Gemini {b['model']}):\n{b['description']}")
        try:
            try:  # fast judge first (Groq); Gemini only if Groq fails
                text, judge_model = _judge_groq(image_bytes, mime, judge_input, groq_key)
                json.loads(text)
            except Exception:
                text, judge_model = _gemini_call(
                    _gemini_client(gemini_key),
                    [types.Part.from_bytes(data=image_bytes, mime_type=mime), judge_input],
                    JUDGE_PROMPT, json_mode=True,
                )
            j = json.loads(text)
            final = str(j.get("description", "")).strip()
            if final:
                out.update(
                    description=final, status="verified", judge_model=judge_model,
                    winner=j.get("winner", "merged"), agreement=j.get("agreement", ""),
                    needs_human_review=bool(j.get("needs_human_review", False)),
                    discrepancies=list(j.get("discrepancies", [])), reasoning=str(j.get("reasoning", "")),
                )
        except Exception as e:
            out["error"] = f"Judge failed ({str(e)[:150]}); showing both descriptions unmerged."
        if out["status"] != "verified":
            # Conservative fallback: concatenate both so no observation is lost.
            out.update(
                description=f"(Two models, not reconciled)\nModel A:\n{a['description']}\n\nModel B:\n{b['description']}",
                status="single_model", needs_human_review=True, agreement="unknown",
            )
    elif a["description"] or b["description"]:
        only = a if a["description"] else b
        out.update(
            description=only["description"], status="single_model", needs_human_review=True, agreement="unverified",
            error=f"Only one model answered. A: {a['error'] or 'ok'} | B: {b['error'] or 'ok'}",
        )
    else:
        out["error"] = f"Both models failed. A: {a['error']} | B: {b['error']}"

    out["seconds"] = round(time.perf_counter() - t0, 2)
    return out
