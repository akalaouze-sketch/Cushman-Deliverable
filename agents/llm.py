"""Unified LLM client for the agent layer — Claude (Anthropic) + OpenAI.

Why both: Claude does the careful extraction/writing; OpenAI (gpt-4.1) gives an
independent second read so we can cross-check values. Keys are read from the
environment (loaded from .env by the entry-point scripts).
"""
from __future__ import annotations

import json
import os
import re

from agents import trace  # auto-records every model call as a span in the active run

# Model defaults (overridable via env). See .env / config.
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
EXTRACT_MODEL = os.environ.get("EXTRACT_MODEL", "claude-opus-4-8")  # extraction → Opus
CLAUDE_ANALYST_MODEL = os.environ.get("CLAUDE_ANALYST_MODEL", "claude-opus-4-8")
OPENAI_MODEL = os.environ.get("OPENAI_VERIFY_MODEL", "gpt-4.1")


def extract_json(text: str):
    """Pull the first JSON object/array out of an LLM response."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    return json.loads(text)


def claude_text(system: str, prompt: str, model: str | None = None, max_tokens: int = 2000,
                temperature: float | None = None, web_search: bool = False) -> str:
    import time
    import anthropic
    client = anthropic.Anthropic()
    kw = {"temperature": temperature} if temperature is not None else {}
    if web_search:  # Anthropic-executed server tool — lets the model pull current data + cite it
        kw["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}]
    mdl = model or CLAUDE_MODEL
    with trace.span(f"claude:{mdl}", kind="llm", input={"system": system, "prompt": prompt},
                    model=mdl, max_tokens=max_tokens, web_search=web_search) as sp:
        last = None
        for attempt in range(5):  # retry transient overload/rate-limit (Anthropic 429/5xx/529)
            try:
                msg = client.messages.create(
                    model=mdl, max_tokens=max_tokens,
                    system=system, messages=[{"role": "user", "content": prompt}], **kw,
                )
                text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
                u = getattr(msg, "usage", None)
                sp.set(attempts=attempt + 1, stop_reason=getattr(msg, "stop_reason", None),
                       input_tokens=getattr(u, "input_tokens", None),
                       output_tokens=getattr(u, "output_tokens", None)).output(text)
                return text
            except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
                last = e
                status = getattr(e, "status_code", None)
                if isinstance(e, anthropic.APIConnectionError) or status in (429, 500, 502, 503, 529):
                    sp.set(retry=attempt + 1, last_status=status)
                    time.sleep(2 * (attempt + 1))
                    continue
                raise
        raise last


def claude_json(system: str, prompt: str, model: str | None = None, max_tokens: int = 4000,
                temperature: float | None = None, web_search: bool = False):
    return extract_json(claude_text(system, prompt, model=model, max_tokens=max_tokens,
                                    temperature=temperature, web_search=web_search))


def claude_vision_json(system: str, prompt: str, image_bytes: bytes, model: str | None = None,
                       max_tokens: int = 2000, media_type: str = "image/png"):
    """Send an image + prompt to Claude vision and parse a JSON reply."""
    import base64
    import anthropic
    client = anthropic.Anthropic()
    b64 = base64.standard_b64encode(image_bytes).decode()
    mdl = model or CLAUDE_MODEL
    with trace.span(f"claude-vision:{mdl}", kind="llm", input={"system": system, "prompt": prompt},
                    model=mdl, image_bytes=len(image_bytes), media_type=media_type) as sp:
        msg = client.messages.create(
            model=mdl, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text", "text": prompt},
            ]}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        sp.output(text)
        return extract_json(text)


def openai_json(system: str, prompt: str, model: str | None = None, max_tokens: int = 4000,
                temperature: float | None = None):
    from openai import OpenAI
    client = OpenAI()
    kw = {"temperature": temperature} if temperature is not None else {}
    mdl = model or OPENAI_MODEL
    with trace.span(f"openai:{mdl}", kind="llm", input={"system": system, "prompt": prompt},
                    model=mdl, max_tokens=max_tokens, json=True) as sp:
        resp = client.chat.completions.create(
            model=mdl, max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}], **kw,
        )
        content = resp.choices[0].message.content
        u = getattr(resp, "usage", None)
        sp.set(input_tokens=getattr(u, "prompt_tokens", None),
               output_tokens=getattr(u, "completion_tokens", None)).output(content)
        return json.loads(content)


def openai_text(system: str, prompt: str, model: str | None = None, max_tokens: int = 2000,
                temperature: float | None = None) -> str:
    from openai import OpenAI
    client = OpenAI()
    kw = {"temperature": temperature} if temperature is not None else {}
    mdl = model or OPENAI_MODEL
    with trace.span(f"openai:{mdl}", kind="llm", input={"system": system, "prompt": prompt},
                    model=mdl, max_tokens=max_tokens) as sp:
        resp = client.chat.completions.create(
            model=mdl, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}], **kw,
        )
        text = resp.choices[0].message.content.strip()
        u = getattr(resp, "usage", None)
        sp.set(input_tokens=getattr(u, "prompt_tokens", None),
               output_tokens=getattr(u, "completion_tokens", None)).output(text)
        return text
