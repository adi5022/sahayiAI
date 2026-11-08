"""LLM call for the Sahayi agent (Groq-hosted GPT-OSS, with fallback to other Groq models). The prompt lives in prompt.py."""
from bia_hackaton_2026.llm.groq_chat import AGENT_CHAIN, chain_from, chat
from bia_hackaton_2026.llm.prompt import SYSTEM_PROMPT

MODEL = "openai/gpt-oss-120b"
MODELS = AGENT_CHAIN


def ask_llm(
    user_input: str,
    history: list[dict] = (),
    model: str = MODEL,
    api_key: str | None = None,
    system_prompt: str = SYSTEM_PROMPT,
    background: str = "",
) -> str:
    """Returns the model's JSON reply as a string (parsed/validated by the caller).
    `model` is tried first; if it is rate limited or down, the other models in AGENT_CHAIN are tried in turn."""
    if background:  # long-term member notes, kept apart from the conversation turns
        system_prompt = system_prompt + "\n\n" + background
    messages = [{"role": "system", "content": system_prompt}, *history, {"role": "user", "content": user_input}]
    return chat(messages, chain_from(model, AGENT_CHAIN), api_key, json_mode=True, temperature=0.2)
