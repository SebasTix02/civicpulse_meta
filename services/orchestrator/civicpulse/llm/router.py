"""LLMRouter: OpenAI-compatible chat calls over the Muse provider chain with a circuit breaker.

Chain (2026-10-27): Muse Spark Open on Baseten -> Muse Spark 1.3 on the Meta Model API ->
Muse Spark Open on Fireworks -> Modal Endpoints -> Muse Glimmer. If every provider fails, the
caller falls back to catalog keywords (text) or leaves the classifier off (vision): states
stay in *_REQUIRES_REVIEW and a confirmation is never invented.
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel, ValidationError

from civicpulse.config import ProviderConfig


class AllProvidersFailed(RuntimeError):
    pass


@dataclass
class Breaker:
    """Opens after `threshold` consecutive failures and stays open for `cooldown_s`."""

    threshold: int = 3
    cooldown_s: float = 120.0
    failures: int = 0
    opened_at: float | None = None

    def available(self, now: float) -> bool:
        if self.opened_at is None:
            return True
        if now - self.opened_at >= self.cooldown_s:
            self.opened_at, self.failures = None, 0
            return True
        return False

    def record(self, ok: bool, now: float) -> None:
        if ok:
            self.failures, self.opened_at = 0, None
            return
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = now


@dataclass
class CallResult:
    data: BaseModel
    provider: str
    model: str
    latency_ms: int
    attempts: list[dict] = field(default_factory=list)


def image_part(jpeg: bytes) -> dict:
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}


def extract_json(text: str) -> dict:
    """Models sometimes wrap JSON in prose or code fences; take the outermost object.
    Reasoning normally comes apart in `reasoning_content`; inline <think> blocks are dropped."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in answer")
    return json.loads(match.group(0))


class LLMRouter:
    def __init__(self, providers: list[ProviderConfig], client: httpx.Client | None = None, clock=time.monotonic):
        self.providers = providers
        self.client = client or httpx.Client(timeout=httpx.Timeout(90.0, connect=5.0))  # reasoning is slow
        self.clock = clock
        self.breakers = {p.name: Breaker() for p in providers}

    def _chat(self, p: ProviderConfig, messages: list[dict], max_tokens: int) -> str:
        body = {"model": p.model, "messages": messages, "temperature": 0,
                "max_tokens": max_tokens + p.reasoning_min_tokens}
        if p.reasoning_effort:
            body["reasoning_effort"] = p.reasoning_effort
        resp = self.client.post(f"{p.base_url.rstrip('/')}/chat/completions",
                                headers={"Authorization": f"Bearer {p.api_key}"}, json=body)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"] or ""  # None when reasoning ate the budget

    def structured(self, system: str, user: list[dict] | str, schema: type[BaseModel], *,
                   vision: bool = False, max_tokens: int = 400) -> CallResult:
        """Ask for JSON matching `schema`. A validation error gets one retry with the error
        shown to the model, then the next provider."""
        attempts: list[dict] = []
        for p in self.providers:
            if vision and not p.vision:
                continue
            if not p.api_key:
                attempts.append({"provider": p.name, "error": "no_api_key"})
                continue
            breaker = self.breakers[p.name]
            if not breaker.available(self.clock()):
                attempts.append({"provider": p.name, "error": "circuit_open"})
                continue
            messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            started = self.clock()
            for retry in range(2):
                text = ""
                try:
                    text = self._chat(p, messages, max_tokens)
                    data = schema.model_validate(extract_json(text))
                    breaker.record(True, self.clock())
                    return CallResult(data, p.name, p.model, int((self.clock() - started) * 1000), attempts)
                except (ValidationError, ValueError) as exc:
                    attempts.append({"provider": p.name, "error": f"invalid_output: {str(exc)[:200]}"})
                    if retry == 0:
                        messages = messages + [
                            {"role": "assistant", "content": text},
                            {"role": "user", "content": f"Your JSON was invalid: {str(exc)[:500]}. Answer again with JSON only."},
                        ]
                        continue
                    breaker.record(False, self.clock())
                except (httpx.HTTPError, KeyError) as exc:
                    attempts.append({"provider": p.name, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
                    breaker.record(False, self.clock())
                    break
        raise AllProvidersFailed(json.dumps(attempts)[:2000])
