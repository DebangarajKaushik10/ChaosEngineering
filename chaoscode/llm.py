"""Tiny LLM wrapper: one function, ask_json(system, user) -> dict.

Providers:
  anthropic  - needs ANTHROPIC_API_KEY
  openai     - needs OPENAI_API_KEY; set OPENAI_BASE_URL for any OpenAI-compatible
               server, e.g. Ollama (http://localhost:11434/v1, key 'ollama'),
               Groq, or Gemini's OpenAI endpoint
  none       - no LLM; agent.py falls back to its rule-based planner
"""
import json
import os
import re

import config

DEFAULT_MODELS = {"anthropic": "claude-sonnet-5-5", "openai": "gpt-4o-mini"}


def _extract_json(text):
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        text = text[start:end + 1]
    return json.loads(text)


def ask_text(system, user, max_tokens=800):
    provider = config.LLM_PROVIDER
    model = config.LLM_MODEL or DEFAULT_MODELS.get(provider, "")
    if provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                      messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in resp.content if b.type == "text")
    if provider == "openai":
        from openai import OpenAI
        client = OpenAI(base_url=os.getenv("OPENAI_BASE_URL") or None)
        resp = client.chat.completions.create(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        return resp.choices[0].message.content
    raise RuntimeError("LLM_PROVIDER is 'none'")


def ask_json(system, user, max_tokens=2000):
    return _extract_json(ask_text(system, user, max_tokens))
