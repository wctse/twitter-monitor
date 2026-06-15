import asyncio
import logging
from urllib.parse import quote

import aiohttp


logger = logging.getLogger(__name__)


X_API_BASE_URL = "https://api.x.com/2"
FALLBACK_API_BASE_URL = "https://api.twitter.com/2"


class XAPIClient:
    def __init__(self, cfg: dict):
        self.bearer_token = str(cfg.get("bearer_token") or "").strip()
        self.base_url = str(cfg.get("base_url") or X_API_BASE_URL).rstrip("/")
        self.fallback_base_url = str(cfg.get("fallback_base_url") or FALLBACK_API_BASE_URL).rstrip("/")
        self.max_results = int(cfg.get("max_results", 10))
        self.exclude_retweets = bool(cfg.get("exclude_retweets", True))
        self.exclude_replies = bool(cfg.get("exclude_replies", True))
        self.retry_max_attempts = max(1, int(cfg.get("retry_max_attempts", 3)))
        self.retry_backoff_base_seconds = float(cfg.get("retry_backoff_base_seconds", 1.0))
        self.retry_backoff_max_seconds = float(cfg.get("retry_backoff_max_seconds", 8.0))
        self._session: aiohttp.ClientSession | None = None
        self._user_cache: dict[str, dict] = {}

    async def open(self, connector: aiohttp.BaseConnector | None = None):
        timeout = aiohttp.ClientTimeout(connect=10, sock_read=30)
        self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def resolve_user(self, source_cfg: dict) -> dict | None:
        user_id = str(source_cfg.get("user_id") or "").strip()
        username = str(source_cfg.get("username") or "").strip().lstrip("@")

        if user_id:
            cache_key = f"id:{user_id}"
            if cache_key in self._user_cache:
                return self._user_cache[cache_key]
            user = await self._get_json(f"/users/{quote(user_id)}", params={"user.fields": "username,name"})
        elif username:
            cache_key = f"username:{username.lower()}"
            if cache_key in self._user_cache:
                return self._user_cache[cache_key]
            user = await self._get_json(
                f"/users/by/username/{quote(username)}",
                params={"user.fields": "username,name"},
            )
        else:
            return None

        data = user.get("data") if user else None
        if not isinstance(data, dict) or not data.get("id"):
            return None

        normalized = {
            "id": str(data.get("id")),
            "username": str(data.get("username") or username),
            "name": str(data.get("name") or source_cfg.get("name") or username or user_id),
        }
        self._user_cache[f"id:{normalized['id']}"] = normalized
        if normalized["username"]:
            self._user_cache[f"username:{normalized['username'].lower()}"] = normalized
        return normalized

    async def fetch_recent_posts(self, user_id: str, since_id: str | None = None) -> list[dict]:
        params = {
            "max_results": str(max(5, min(100, self.max_results))),
            "tweet.fields": "created_at,entities,referenced_tweets,lang,public_metrics",
        }
        excludes = []
        if self.exclude_retweets:
            excludes.append("retweets")
        if self.exclude_replies:
            excludes.append("replies")
        if excludes:
            params["exclude"] = ",".join(excludes)
        if since_id:
            params["since_id"] = since_id

        payload = await self._get_json(f"/users/{quote(user_id)}/tweets", params=params)
        data = payload.get("data") if payload else None
        if not isinstance(data, list):
            return []
        return [self._normalize_post(item, user_id) for item in data if isinstance(item, dict)]

    def _normalize_post(self, item: dict, user_id: str) -> dict:
        post_id = str(item.get("id") or "")
        text = str(item.get("text") or "").strip()
        username = ""
        for user in self._user_cache.values():
            if user.get("id") == user_id:
                username = str(user.get("username") or "")
                break
        post_url = f"https://x.com/{username}/status/{post_id}" if username else f"https://x.com/i/web/status/{post_id}"
        referenced_tweets = item.get("referenced_tweets") or []
        return {
            "post_id": post_id,
            "post_url": post_url,
            "title": _make_title(text),
            "text": text,
            "published_at": item.get("created_at"),
            "metrics": item.get("public_metrics") or {},
            "referenced_tweets": referenced_tweets if isinstance(referenced_tweets, list) else [],
        }

    async def _get_json(self, path: str, params: dict | None = None) -> dict:
        if not self.bearer_token:
            raise ValueError("X API bearer token is missing. Set x.bearer_token in config.yaml")
        if not self._session:
            raise RuntimeError("XAPIClient.open() must be called before use")

        last_error = None
        for attempt in range(1, self.retry_max_attempts + 1):
            try:
                return await self._request_json(self.base_url, path, params or {})
            except aiohttp.ClientResponseError as e:
                last_error = e
                if e.status == 404 and self.base_url != self.fallback_base_url:
                    return await self._request_json(self.fallback_base_url, path, params or {})
                if e.status not in {408, 409, 425, 429, 500, 502, 503, 504, 529}:
                    raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = e

            if attempt >= self.retry_max_attempts:
                raise last_error

            await asyncio.sleep(self._retry_delay_seconds(attempt))

        raise RuntimeError("X API request failed")

    async def _request_json(self, base_url: str, path: str, params: dict) -> dict:
        headers = {"Authorization": f"Bearer {self.bearer_token}"}
        async with self._session.get(f"{base_url}{path}", headers=headers, params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise aiohttp.ClientResponseError(
                    request_info=resp.request_info,
                    history=resp.history,
                    status=resp.status,
                    message=text[:500],
                    headers=resp.headers,
                )
            payload = await resp.json()
            return payload if isinstance(payload, dict) else {}

    def _retry_delay_seconds(self, attempt: int) -> float:
        delay = self.retry_backoff_base_seconds * (2 ** (attempt - 1))
        return min(delay, self.retry_backoff_max_seconds)


def _make_title(text: str) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        return "Untitled X post"
    if len(cleaned) <= 96:
        return cleaned
    return f"{cleaned[:93]}..."
