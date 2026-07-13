"""Shared DeepSeek LLM client — single source of truth for API key and LLM calls."""

import json
import os
from pathlib import Path


def get_api_key() -> str | None:
    """Read DeepSeek API key from env or .env file."""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DEEPSEEK_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def call_llm(system_prompt: str, user_prompt: str, temperature: float = 0.3) -> str | None:
    """Call DeepSeek API. Returns None if unavailable."""
    key = get_api_key()
    if not key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url="https://api.deepseek.com")
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=8192,
        )
        return resp.choices[0].message.content
    except Exception as e:
        print(f"[DeepSeek] Error: {e}")
        return None


def extract_json(text: str) -> dict | None:
    """Extract JSON object from LLM response (may be wrapped in markdown fences)."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(l for l in lines if not l.startswith("```"))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    return None
