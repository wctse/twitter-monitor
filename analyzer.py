import asyncio
import json
import logging
import random
import re

import aiohttp


logger = logging.getLogger(__name__)


class _RetryableLLMError(RuntimeError):
    pass


class LLMAnalyzer:
    def __init__(self, llm_cfg: dict):
        self.provider = llm_cfg.get("provider", "api")
        self.base_url = llm_cfg["base_url"].rstrip("/")
        self.model = llm_cfg["model"]
        self.api_key = llm_cfg.get("api_key") or ""
        self.timeout_seconds = int(llm_cfg.get("timeout", 180))
        prompt = llm_cfg.get("prompt")
        if not prompt or not str(prompt).strip():
            raise ValueError("LLM prompt is missing. Set llm.prompt_file in config.yaml.")
        self.prompt = str(prompt).strip()
        self.retry_max_attempts = max(1, int(llm_cfg.get("retry_max_attempts", 3)))
        self.retry_backoff_base_seconds = float(llm_cfg.get("retry_backoff_base_seconds", 1.0))
        self.retry_backoff_max_seconds = float(llm_cfg.get("retry_backoff_max_seconds", 8.0))
        self.retry_backoff_jitter_seconds = float(llm_cfg.get("retry_backoff_jitter_seconds", 0.25))
        self.retry_status_codes = {
            int(x) for x in llm_cfg.get("retry_status_codes", [408, 409, 425, 429, 500, 502, 503, 504, 529])
        }
        self._session: aiohttp.ClientSession | None = None
        self.consecutive_failures: int = 0

    async def open(self, connector: aiohttp.BaseConnector | None = None):
        timeout = aiohttp.ClientTimeout(connect=10, sock_read=self.timeout_seconds)
        self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def analyze(self, post_text: str, post_title: str = "") -> dict | None:
        if not post_text.strip():
            return None

        user_content = post_text
        if post_title:
            user_content = f"X post: {post_title}\n\n{post_text}"

        for attempt in range(1, self.retry_max_attempts + 1):
            try:
                raw = await self._call(user_content)
                if not raw:
                    return None

                parsed = self._parse(raw)
                if parsed:
                    self.consecutive_failures = 0
                    return parsed

                raise _RetryableLLMError("invalid JSON response")
            except (aiohttp.ClientError, asyncio.TimeoutError, _RetryableLLMError) as e:
                reason = str(e) or e.__class__.__name__
                if attempt >= self.retry_max_attempts:
                    logger.error("LLM failed after %d attempt(s): %s", self.retry_max_attempts, reason)
                    self.consecutive_failures += 1
                    return None

                delay = self._retry_delay_seconds(attempt)
                logger.warning(
                    "LLM attempt %d/%d failed (%s); retrying in %.2fs",
                    attempt,
                    self.retry_max_attempts,
                    reason,
                    delay,
                )
                await asyncio.sleep(delay)
            except Exception as e:
                logger.error("LLM error: %r", e)
                self.consecutive_failures += 1
                return None
        self.consecutive_failures += 1
        return None

    def _retry_delay_seconds(self, attempt: int) -> float:
        delay = self.retry_backoff_base_seconds * (2 ** (attempt - 1))
        delay = min(delay, self.retry_backoff_max_seconds)
        if self.retry_backoff_jitter_seconds > 0:
            delay += random.uniform(0.0, self.retry_backoff_jitter_seconds)
        return delay

    def _is_retryable_status(self, status_code: int) -> bool:
        return status_code in self.retry_status_codes

    async def _call(self, user_content: str) -> str | None:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        messages = [
            {"role": "system", "content": self.prompt},
            {"role": "user", "content": user_content},
        ]

        if self.provider == "ollama":
            url = f"{self.base_url}/api/chat"
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.1},
            }
            async with self._session.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    if self._is_retryable_status(resp.status):
                        raise _RetryableLLMError(f"HTTP {resp.status}: {error_text[:200]}")
                    logger.error("LLM API error %d: %s", resp.status, error_text)
                    return None
                data = await resp.json()
            content = data.get("message", {}).get("content", "")
            if not content:
                raise _RetryableLLMError("empty response content")
            return content

        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "stream": True,
            "response_format": {"type": "json_object"},
        }
        async with self._session.post(url, json=payload, headers=headers) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                if self._is_retryable_status(resp.status):
                    raise _RetryableLLMError(f"HTTP {resp.status}: {error_text[:200]}")
                logger.error("LLM API error %d: %s", resp.status, error_text)
                return None
            parts: list[str] = []
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line == "data: [DONE]":
                    continue
                if not line.startswith("data: "):
                    continue
                try:
                    chunk = json.loads(line[6:])
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    if text := delta.get("content"):
                        parts.append(text)
                except (json.JSONDecodeError, IndexError):
                    continue
        content = "".join(parts)
        if not content:
            raise _RetryableLLMError("empty streaming response")
        return content

    @staticmethod
    def _parse(content: str) -> dict | None:
        try:
            stripped = content.strip()
            if stripped.startswith("```"):
                stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
                stripped = re.sub(r"\s*```\s*$", "", stripped)
            result = json.loads(stripped)
            tickers = []
            for t in result.get("tickers", []):
                if not isinstance(t, dict):
                    continue
                bias = str(t.get("bias", "neutral")).lower()
                if bias not in ("bullish", "bearish", "neutral"):
                    bias = "neutral"
                timeframe = str(t.get("timeframe", "") or "").strip().lower()
                if timeframe not in ("", "intraday", "days", "weeks", "months", "quarters", "years"):
                    timeframe = ""
                tickers.append(
                    {
                        "symbol": str(t.get("symbol", "")).upper(),
                        "bias": bias,
                        "thesis": str(t.get("thesis", "")),
                        "timeframe": timeframe,
                        "price_target": str(t.get("price_target", "") or "").strip(),
                    }
                )
            return {
                "is_signal": bool(result.get("is_signal", False)),
                "confidence": float(result.get("confidence", 0.0)),
                "summary": str(result.get("summary", "")),
                "tickers": tickers,
            }
        except (json.JSONDecodeError, ValueError) as e:
            logger.error("Failed to parse LLM response: %s | raw (first 1000 chars): %.1000s", e, content)
            return None
