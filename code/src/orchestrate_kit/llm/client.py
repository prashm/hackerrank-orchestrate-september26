"""Provider-agnostic structured extraction with caching and usage accounting.

    llm = StructuredLLM(AnthropicProvider(), cache=JsonCache("cache/x"), usage=UsageLedger())
    data = llm.extract(name="msg_01", system=..., content=[text("..."), image_png(bytes)],
                       schema={...}, purpose="messages", prompt_version="v1")

Only `AnthropicProvider` talks to a vendor SDK; swap it to change provider.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any, Protocol

from .cache import JsonCache, content_key
from .usage import CallRecord, UsageLedger


def text(s: str) -> dict:
    return {"type": "text", "text": s}


def image_png(data: bytes) -> dict:
    return {"type": "image_png", "data": data}


@dataclass
class Completion:
    data: dict
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class Provider(Protocol):
    name: str
    model: str

    def complete_json(self, system: str, content: list[dict], schema: dict, max_tokens: int) -> Completion: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str | None = None, effort: str = "low"):
        import anthropic  # optional dependency, imported lazily

        self.model = model or os.environ.get("ORCH_MODEL", "claude-opus-5-5")
        self.effort = effort
        self._client = anthropic.Anthropic()

    def _blocks(self, content: list[dict]) -> list[dict]:
        out = []
        for c in content:
            if c["type"] == "image_png":
                out.append({"type": "image", "source": {
                    "type": "base64", "media_type": "image/png",
                    "data": base64.standard_b64encode(c["data"]).decode("ascii")}})
            else:
                out.append(c)
        return out

    def complete_json(self, system: str, content: list[dict], schema: dict, max_tokens: int) -> Completion:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": self._blocks(content)}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"model refused: {getattr(resp, 'stop_details', None)}")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("output truncated at max_tokens")
        raw = next(b.text for b in resp.content if b.type == "text")
        u = resp.usage
        return Completion(
            data=json.loads(raw),
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )


class CacheMiss(LookupError):
    pass


class StructuredLLM:
    def __init__(self, provider: Provider | None, cache: JsonCache, usage: UsageLedger, *, offline: bool = False):
        self.provider = provider
        self.cache = cache
        self.usage = usage
        self.offline = offline

    def extract(self, *, name: str, system: str, content: list[dict], schema: dict,
                purpose: str, prompt_version: str, max_tokens: int = 4000) -> dict:
        model = self.provider.model if self.provider else os.environ.get("ORCH_MODEL", "claude-opus-5-5")
        hashed = [c if c["type"] != "image_png" else {"png_sha": content_key(c["data"])} for c in content]
        key = content_key(model, prompt_version, system, schema, hashed)
        hit = self.cache.get(name, key)
        if hit is not None:
            m = hit["_meta"]
            self.usage.add(CallRecord(m["provider"], m["model"], purpose, m["input_tokens"],
                                      m["output_tokens"], m.get("cache_read_tokens", 0),
                                      m.get("cache_write_tokens", 0), cached=True))
            return hit["data"]
        if self.offline or self.provider is None:
            raise CacheMiss(f"{name}: no cached result and LLM calls are disabled")
        comp = self.provider.complete_json(system, content, schema, max_tokens)
        meta = {"provider": self.provider.name, "model": self.provider.model,
                "input_tokens": comp.input_tokens, "output_tokens": comp.output_tokens,
                "cache_read_tokens": comp.cache_read_tokens, "cache_write_tokens": comp.cache_write_tokens}
        self.cache.put(name, key, {"data": comp.data, "_meta": meta})
        self.usage.add(CallRecord(self.provider.name, self.provider.model, purpose, comp.input_tokens,
                                  comp.output_tokens, comp.cache_read_tokens, comp.cache_write_tokens))
        return comp.data
