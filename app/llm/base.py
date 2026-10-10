from __future__ import annotations

import json
import re
import threading
from typing import Any, Optional, Sequence

from app.llm.usage import ResponseCache, UsageMeter, cache_key, estimate_tokens


class LLMError(RuntimeError):
    """The model could not be reached or returned unusable output."""


class LLMProvider:
    name = "base"
    model = ""
    cache_scope = ""  # keeps one customer's cached replies from being replayed to another
    fast_model = ""  # cheaper model for vision and structure-extraction calls; empty means use `model`
    supports_vision = False
    is_offline = False

    def __init__(self, max_concurrency: int = 4):
        self._gate = threading.BoundedSemaphore(max_concurrency)
        self._tls = threading.local()
        self.totals = UsageMeter()
        self.cache: Optional[ResponseCache] = None

    # -- hooks for concrete providers
    def _report(self, prompt: int, completion: int, cached: int = 0) -> None:
        self._tls.usage = (prompt, completion, cached)

    def _active_model(self) -> str:
        if getattr(self._tls, "tier", "main") == "fast" and self.fast_model:
            return self.fast_model
        return self.model

    def complete(self, system: str, user: str, *, images: Sequence[bytes] = (), json_mode: bool = True,
                 max_tokens: int = 4096, temperature: float = 0.4, meter: Optional[UsageMeter] = None,
                 tier: str = "main") -> str:
        with self._gate:
            self._tls.usage, self._tls.tier = None, tier
            text = self._complete(system, user, images, json_mode, max_tokens, temperature)
        usage = self._tls.usage
        estimated = usage is None
        prompt, completion, cached = usage or (estimate_tokens(system) + estimate_tokens(user), estimate_tokens(text), 0)
        self.totals.add(prompt, completion, cached, estimated)
        if meter is not None:
            meter.add(prompt, completion, cached, estimated)
        self._tls.last_tokens = prompt + completion
        return text

    def _complete(self, system, user, images, json_mode, max_tokens, temperature) -> str:  # pragma: no cover
        raise NotImplementedError

    def complete_json(self, system: str, user: str, *, images: Sequence[bytes] = (), max_tokens: int = 4096,
                      temperature: float = 0.4, meter: Optional[UsageMeter] = None, tier: str = "main",
                      cache: bool = False) -> Any:
        """Ask for JSON. With cache=True an identical earlier request that produced valid JSON is replayed for free."""
        key = None
        if cache and self.cache is not None:
            key = cache_key((self.fast_model if tier == "fast" and self.fast_model else self.model) + "|" + self.cache_scope, system, user,
                            images, max_tokens, temperature)
            hit = self.cache.get(key)
            if hit is not None:
                try:
                    value = extract_json(hit[0])
                    if meter is not None:
                        meter.add_cache_hit(hit[1])
                    self.totals.add_cache_hit(hit[1])
                    return value
                except ValueError:
                    pass
        text = self.complete(system, user, images=images, json_mode=True, max_tokens=max_tokens,
                             temperature=temperature, meter=meter, tier=tier)
        spent = getattr(self._tls, "last_tokens", 0)
        try:
            value = extract_json(text)
        except ValueError:
            retry = self.complete(
                system, user + "\n\nYour previous reply was not valid JSON. Reply with ONLY one valid JSON value, "
                "no commentary, no code fences. Escape every backslash in LaTeX as \\\\.",
                images=images, json_mode=True, max_tokens=max_tokens, temperature=0.1, meter=meter, tier=tier)
            spent += getattr(self._tls, "last_tokens", 0)
            text = retry
            try:
                value = extract_json(retry)
            except ValueError as exc:
                raise LLMError(f"Model returned invalid JSON: {exc}") from exc
        if key is not None:
            self.cache.put(key, text, self.model, spent)
        return value


def sniff_image_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"GIF8"):
        return "image/gif"
    return "image/png"


# LaTeX commands that begin with a letter JSON also treats as an escape (\f \t \b \n \r). Models often
# forget to double the backslash, which would silently turn "\frac" into a form feed + "rac".
_LATEX_ESCAPE_CLASH = re.compile(
    r"(?<!\\)\\(?=(?:frac|dfrac|tfrac|theta|vartheta|beta|bar|begin|binom|bigcup|bigcap|bullet|because|neq|ne|neg|"
    r"nu|nabla|not|notin|nmid|right|rho|rightarrow|rangle|rceil|rfloor|text|textbf|times|tan|tau|to|top|triangle|"
    r"tilde|therefore|forall|frown|flat|fbox)(?![A-Za-z]))"
)
_INVALID_ESCAPE = re.compile(r'(?<!\\)\\(?!["\\/bfnrt]|u[0-9a-fA-F]{4})')
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def extract_json(text: str) -> Any:
    """Parse the first JSON value in *text*, tolerating code fences, prose and unescaped LaTeX."""
    if not text or not text.strip():
        raise ValueError("empty response")
    s = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.S | re.I)
    if fence:
        s = fence.group(1).strip()
    starts = [i for i in (s.find("{"), s.find("[")) if i >= 0]
    if not starts:
        raise ValueError("no JSON object found")
    s = s[min(starts):]
    decoder = json.JSONDecoder()
    last: Optional[Exception] = None
    # The clash fix must run before the first parse, not only after a failure: "\frac" is a *valid* JSON
    # escape (form feed + "rac"), so plain parsing succeeds and silently corrupts the mathematics.
    clash_fixed = _LATEX_ESCAPE_CLASH.sub(r"\\\\", s)
    repaired = _INVALID_ESCAPE.sub(r"\\\\", clash_fixed)
    candidates = [clash_fixed, repaired, _TRAILING_COMMA.sub(r"\1", repaired), s]
    for cand in candidates:
        try:
            value, _ = decoder.raw_decode(cand)
            return value
        except json.JSONDecodeError as exc:
            last = exc
    raise ValueError(f"invalid JSON ({last})")
