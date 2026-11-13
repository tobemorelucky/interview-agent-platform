"""Round-18 live-model gate: hard caps, redacted capture, offline replay.

Round 17 replaced every model with a replay fixture. Round 18 needs *real*
extraction on two synthetic resumes and two JDs, inside a hard ceiling of
**8 HTTP calls / 150,000 tokens / 25 minutes**. This module is the only place
that counts, and it counts at the provider boundary — the last point before a
request can leave the process — so a refusal is a proven zero-send rather than
an after-the-fact guess.

Three rules the gate enforces:

1. `before()` raises `LIVE_BUDGET_*` with `request_sent = False` **before** any
   HTTP transport is touched. `governed_call` reads that flag, so a refused call
   is not charged as an unknown send.
2. Every call that does go out is captured as a JSON record: route, model,
   request digest/length, response text, usage, latency, status. No API key, no
   Authorization header, no `.env` content, and no full prompt body is stored —
   only a SHA-256 of the request, which is what replay needs to find it again.
3. The wall clock is absolute: once the deadline passes, further calls are
   refused even if calls/tokens remain.

`CaptureReplay` serves those records back deterministically, so a defect seen
online can be reproduced with zero further HTTP.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from interview_api.infrastructure.models.provider import (
    ModelProvider, ModelProviderError, ModelResponse)


# The ceiling stated in the round-18 brief. Kept as a literal so a future edit
# has to be deliberate.
LIMITS = dict(max_calls=8, max_tokens=150_000, max_seconds=25 * 60)


class LiveBudgetExceeded(ModelProviderError):
    """Refused before send: the gate, not the provider, rejected this call."""

    def __init__(self, code: str, message: str):
        super().__init__(code, message)
        # Positive claim: nothing reached the provider, so nothing is charged.
        self.request_sent = False
        self.usage = {"total_tokens": 0}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LiveGate:
    """Counts real calls/tokens against fixed caps and writes redacted captures."""

    root: Path
    limits: dict = field(default_factory=lambda: dict(LIMITS))
    clock: object = time.monotonic
    started: float = field(default_factory=time.monotonic)
    calls: int = 0
    estimated_tokens: int = 0
    records: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ── accounting ──────────────────────────────────────────────────────────
    def elapsed(self) -> float:
        return self.clock() - self.started

    def remaining(self) -> dict:
        return dict(
            calls=self.limits["max_calls"] - self.calls,
            tokens=self.limits["max_tokens"] - self.estimated_tokens,
            seconds=int(self.limits["max_seconds"] - self.elapsed()),
        )

    @staticmethod
    def estimate(messages: list[dict], max_output_tokens: int) -> int:
        """Deliberately conservative: UTF-8 bytes // 2 approximates CJK tokens.

        Production governance reserves by raw byte length against a 1M budget.
        Here the number only has to be an upper bound that never lets the run
        exceed 150k, so halving the byte count is safe for Chinese text and
        still leaves room for eight ~6k-output calls.
        """
        body = json.dumps(messages, ensure_ascii=False).encode("utf-8")
        return len(body) // 2 + max(int(max_output_tokens or 0), 1)

    def before(self, *, route: str, model: str, messages: list[dict],
               max_output_tokens: int) -> dict:
        """Reserve one call or refuse. Never touches the network."""
        if self.elapsed() > self.limits["max_seconds"]:
            raise LiveBudgetExceeded(
                "LIVE_BUDGET_TIME",
                f"超过本轮 {self.limits['max_seconds'] // 60} 分钟真实模型时限，已停止发送")
        if self.calls >= self.limits["max_calls"]:
            raise LiveBudgetExceeded(
                "LIVE_BUDGET_CALLS",
                f"超过本轮 {self.limits['max_calls']} 次真实模型调用上限，已停止发送")
        estimate = self.estimate(messages, max_output_tokens)
        if self.estimated_tokens + estimate > self.limits["max_tokens"]:
            raise LiveBudgetExceeded(
                "LIVE_BUDGET_TOKENS",
                f"预计累计 {self.estimated_tokens + estimate} 会超过 "
                f"{self.limits['max_tokens']} token 上限，已停止发送")
        self.calls += 1
        self.estimated_tokens += estimate
        body = json.dumps(messages, ensure_ascii=False)
        return dict(seq=self.calls, route=route, model=model, prompt_sha256=_digest(body),
                    request_bytes=len(body.encode("utf-8")), estimated_tokens=estimate,
                    started_at=_now(), started_monotonic=self.clock())

    def after(self, reservation: dict, *, text: str, usage: dict | None,
              status: str = "SUCCEEDED", error: str | None = None) -> dict:
        record = dict(reservation,
                      status=status, error=error,
                      latency_ms=round((self.clock() - reservation["started_monotonic"]) * 1000),
                      response_sha256=_digest(text), response_chars=len(text),
                      response_text=text, usage=usage or {})
        record.pop("started_monotonic", None)
        self.records.append(record)
        self._write(record)
        return record

    def _write(self, record: dict) -> None:
        name = f"{record['seq']:02d}-{record['route']}" + (
            "" if record["status"] == "SUCCEEDED" else "-" + record["status"].lower())
        (self.root / f"{name}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")

    def summary(self) -> dict:
        ok = [r for r in self.records if r["status"] == "SUCCEEDED"]
        return dict(
            limits=self.limits, calls=self.calls, estimated_tokens=self.estimated_tokens,
            elapsed_seconds=round(self.elapsed()),
            succeeded=len(ok), failed=len(self.records) - len(ok),
            routes=[f"{r['seq']}:{r['route']}:{r['status']}" for r in self.records],
            captured_in=str(self.root),
        )


class GatedProvider(ModelProvider):
    """Structured-route wrapper: gate first, then the governed real provider."""

    def __init__(self, inner, gate: LiveGate, route: str):
        self.inner, self.gate, self.route = inner, gate, route
        self.provider_name = inner.provider_name
        self.model_name = inner.model_name
        # structured.py / audit.py read these off the routed provider.
        self.timeout = inner.timeout
        self.max_output_tokens = inner.max_output_tokens
        self.thinking_type = getattr(inner, "thinking_type", None)
        self.governed = True
        self.base_url = getattr(inner, "base_url", "")

    async def generate(self, messages: list[dict], *, schema: dict) -> ModelResponse:
        reservation = self.gate.before(route=self.route, model=self.model_name,
                                       messages=messages, max_output_tokens=self.max_output_tokens)
        try:
            response = await self.inner.generate(messages, schema=schema)
        except BaseException as exc:
            self.gate.after(reservation, text="", usage=getattr(exc, "usage", None) or {},
                            status="CANCELLED" if getattr(exc, "code", "") == "model_timeout" else "FAILED",
                            error=getattr(exc, "code", type(exc).__name__))
            raise
        self.gate.after(reservation, text=response.text, usage=response.usage)
        return response


def gated_chat_provider(base_cls, gate: LiveGate, route: str = "resume_extract"):
    """Factory used to patch the SDK-backed `OpenAICompatibleLLMProvider`.

    The worker constructs it with keyword arguments only, so a factory with the
    same signature is a faithful replacement.
    """

    class _GatedChat(base_cls):
        async def chat(self, messages, **kwargs):
            reservation = gate.before(route=route, model=self.model_name, messages=messages,
                                      max_output_tokens=kwargs.get("max_tokens", self.max_output_tokens))
            try:
                text = await super().chat(messages, **kwargs)
            except BaseException as exc:
                gate.after(reservation, text="", usage={}, status="FAILED",
                           error=getattr(exc, "code", type(exc).__name__))
                raise
            # The SDK path does not surface usage here; the durable
            # model_admissions row is the authoritative token record.
            gate.after(reservation, text=text, usage={"total_tokens": None})
            return text

    return _GatedChat


@dataclass
class CaptureReplay(ModelProvider):
    """Replay captured responses offline; no network, deterministic order."""

    root: Path
    route: str = "resume_extract"
    provider_name: str = "capture"
    model_name: str = "round18-capture"
    timeout: float = 60.0
    max_output_tokens: int = 6000

    def __post_init__(self):
        self.root = Path(self.root)
        self.records = []
        for path in sorted(self.root.glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("status") == "SUCCEEDED" and record.get("response_text"):
                self.records.append(record)
        self.cursor = 0

    def select(self, messages: list[dict]) -> dict | None:
        sha = _digest(json.dumps(messages, ensure_ascii=False))
        for record in self.records:
            if record["prompt_sha256"] == sha:
                return record
        if self.cursor < len(self.records):
            record = self.records[self.cursor]
            self.cursor += 1
            return record
        return None

    async def generate(self, messages: list[dict], *, schema: dict) -> ModelResponse:
        record = self.select(messages)
        if record is None:
            raise ModelProviderError("capture_replay_exhausted",
                                     "没有可用的采集记录，无法离线复现该次调用")
        return ModelResponse(record["response_text"], {"total_tokens": 0})
