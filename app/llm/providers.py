from __future__ import annotations

import base64
import logging
import time
from typing import Any, Dict, Sequence

import httpx

from app.llm.base import LLMError, LLMProvider, sniff_image_type

log = logging.getLogger("examforge.llm")
_RETRY_STATUS = {429, 500, 502, 503, 504, 529}


def _explain(status: int, body: str) -> str:
    """Readable error text; an HTML body means a proxy or firewall answered, not the model provider."""
    if body.lstrip()[:15].lower().startswith(("<html", "<!doctype", "<head", "<script")) or "<html" in body[:200].lower():
        return (f"Model API error {status}: the request was answered by a web page, not an AI provider - usually a "
                "company firewall/proxy or a wrong Base URL. Check the Base URL, or ask IT to allow this address.")
    if status in (401, 403):
        return f"Model API error {status}: the provider refused the key. Check the key, its permissions and the model name. {body[:200]}"
    return f"Model API error {status}: {body}"


def _post_with_retry(client: httpx.Client, url: str, headers: Dict[str, str], payload: Dict[str, Any],
                     adjust=None, attempts: int = 4) -> Dict[str, Any]:
    """POST with exponential backoff; *adjust* may rewrite the payload after a 400 (provider quirks)."""
    adjustments = 0
    for attempt in range(attempts + 3):
        try:
            resp = client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            if attempt >= attempts - 1:
                raise LLMError(f"Network error talking to model: {exc.__class__.__name__}") from exc
            time.sleep(min(2 ** attempt, 20))
            continue
        if resp.status_code == 200:
            return resp.json()
        body = resp.text[:600]
        if resp.status_code == 400 and adjust and adjustments < 3 and adjust(payload, body):
            adjustments += 1
            continue
        if resp.status_code in _RETRY_STATUS and attempt < attempts - 1:
            retry_after = resp.headers.get("retry-after")
            delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else 2 ** attempt
            time.sleep(min(delay, 30))
            continue
        raise LLMError(_explain(resp.status_code, body))
    raise LLMError("Model API did not respond successfully.")


class OpenAICompatProvider(LLMProvider):
    """OpenAI, Azure OpenAI-compatible gateways, Ollama, vLLM, LM Studio, Gemini's OpenAI endpoint, etc."""

    name = "openai"
    supports_vision = True

    def __init__(self, api_key: str, base_url: str, model: str, timeout: int = 180, max_concurrency: int = 4,
                 fast_model: str = ""):
        super().__init__(max_concurrency)
        self._key, self._base, self.model, self.fast_model = api_key, base_url, model, fast_model
        self._client = httpx.Client(timeout=timeout)

    def _complete(self, system, user, images: Sequence[bytes], json_mode, max_tokens, temperature) -> str:
        content: Any = user
        if images:
            content = [{"type": "text", "text": user}] + [
                {"type": "image_url", "image_url": {
                    "url": f"data:{sniff_image_type(i)};base64,{base64.b64encode(i).decode()}"}}
                for i in images
            ]
        payload: Dict[str, Any] = {
            "model": self._active_model(),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode and not images:
            payload["response_format"] = {"type": "json_object"}

        def adjust(p: Dict[str, Any], body: str) -> bool:
            low = body.lower()
            if "max_completion_tokens" in low and "max_tokens" in p:
                p["max_completion_tokens"] = p.pop("max_tokens")
                return True
            if "temperature" in low and "temperature" in p:
                p.pop("temperature")
                return True
            if "response_format" in low and "response_format" in p:
                p.pop("response_format")
                return True
            return False

        data = _post_with_retry(
            self._client, f"{self._base}/chat/completions",
            {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}, payload, adjust)
        try:
            usage = data.get("usage") or {}
            if "prompt_tokens" in usage:
                cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
                self._report(int(usage["prompt_tokens"]), int(usage.get("completion_tokens", 0)), int(cached))
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Unexpected response shape from model API.") from exc


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    supports_vision = True

    def __init__(self, api_key: str, base_url: str, model: str, timeout: int = 180, max_concurrency: int = 4,
                 fast_model: str = ""):
        super().__init__(max_concurrency)
        self._key, self._base, self.model, self.fast_model = api_key, base_url, model, fast_model
        self._client = httpx.Client(timeout=timeout)

    def _complete(self, system, user, images: Sequence[bytes], json_mode, max_tokens, temperature) -> str:
        blocks: list = [
            {"type": "image", "source": {"type": "base64", "media_type": sniff_image_type(i),
                                          "data": base64.b64encode(i).decode()}}
            for i in images
        ]
        blocks.append({"type": "text", "text": user})
        payload: Dict[str, Any] = {
            "model": self._active_model(), "max_tokens": max_tokens, "temperature": temperature,
            # The long, unchanging instructions are cacheable: later calls in the same job read them at a fraction of the price.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": blocks}],
        }

        def adjust(p: Dict[str, Any], body: str) -> bool:
            if "temperature" in body.lower() and "temperature" in p:
                p.pop("temperature")
                return True
            return False

        data = _post_with_retry(
            self._client, f"{self._base}/v1/messages",
            {"x-api-key": self._key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            payload, adjust)
        try:
            u = data.get("usage") or {}
            if "input_tokens" in u:
                cached = int(u.get("cache_read_input_tokens", 0) or 0)
                prompt = int(u["input_tokens"]) + cached + int(u.get("cache_creation_input_tokens", 0) or 0)
                self._report(prompt, int(u.get("output_tokens", 0)), cached)
            return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
        except (KeyError, TypeError) as exc:
            raise LLMError("Unexpected response shape from model API.") from exc
