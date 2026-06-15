import asyncio
import logging
import os
import ssl
import sys

import yaml

from analyzer import LLMAnalyzer
from db import (
    bump_attempts,
    get_recent_ticker_mentions,
    has_any_posts,
    init_db,
    is_processed,
    mark_processed,
    record_signal_tickers,
)
from notifier import (
    send_error_alert,
    send_seed_report,
    send_signal,
)
from x_client import XAPIClient


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


LLM_MAX_POST_ATTEMPTS = 3
DEFAULT_PROMPT_FILE = "prompt.yaml"


def _load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _load_prompt_text(path: str) -> str:
    with open(path) as f:
        raw = f.read()
    if path.endswith((".yaml", ".yml")):
        data = yaml.safe_load(raw) or {}
        prompt = data.get("prompt")
        if prompt is None:
            raise ValueError(f"Prompt file {path} must contain a 'prompt' field")
        return str(prompt).strip()
    return raw.strip()


def _build_llm_config(cfg: dict, config_path: str = "config.yaml") -> dict:
    llm_cfg = dict(cfg["llm"])
    prompt = llm_cfg.get("prompt")
    if prompt and str(prompt).strip():
        return llm_cfg

    prompt_file = llm_cfg.get("prompt_file") or DEFAULT_PROMPT_FILE
    prompt_path = prompt_file
    if not os.path.isabs(prompt_path):
        prompt_path = os.path.join(os.path.dirname(os.path.abspath(config_path)), prompt_path)
    llm_cfg["prompt"] = _load_prompt_text(prompt_path)
    return llm_cfg


def _resolve_target_channel_id(cfg: dict) -> int | None:
    tg = cfg.get("telegram", {})
    target_channel_id = tg.get("target_channel_id")
    if target_channel_id is None:
        logger.warning("telegram.target_channel_id is missing — Telegram delivery disabled")
        return None
    try:
        return int(target_channel_id)
    except (TypeError, ValueError):
        logger.warning("telegram.target_channel_id is invalid — Telegram delivery disabled")
        return None


def _make_ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


async def _seed_posts(
    source_id: str,
    source_name: str,
    posts: list[dict],
    db_path: str,
    bot,
    admin_chat_id: int | None,
):
    urls = [p["post_url"] for p in posts]
    logger.info("First scan for %s: seeding %d post(s) without analyzing", source_name, len(posts))
    for p in posts:
        mark_processed(
            source_id=source_id,
            post_id=p["post_id"],
            post_url=p["post_url"],
            post_title=p["title"],
            published_at=p.get("published_at"),
            content_chars=0,
            db_path=db_path,
        )

    if admin_chat_id:
        await send_seed_report(bot, admin_chat_id, source_name, urls)


async def _maybe_alert(
    bot,
    admin_chat_id: int | None,
    error_alerts_enabled: bool,
    message: str,
):
    if admin_chat_id and error_alerts_enabled:
        try:
            await send_error_alert(bot, admin_chat_id, message)
        except Exception as e:
            logger.error("Failed to send admin alert: %r", e)


async def _scan_source(
    x_client: XAPIClient,
    source_cfg: dict,
    twitter_cfg: dict,
    db_path: str,
    analyzer: LLMAnalyzer,
    bot,
    target_channel_id: int | None,
    admin_chat_id: int | None,
    error_alerts_enabled: bool,
):
    resolved_user = await x_client.resolve_user(source_cfg)
    if not resolved_user:
        logger.warning("Skipping source that could not be resolved: %s", source_cfg)
        await _maybe_alert(
            bot,
            admin_chat_id,
            error_alerts_enabled,
            f"Could not resolve X source: {source_cfg}",
        )
        return

    username = str(resolved_user.get("username") or "").lstrip("@")
    source_id = str(source_cfg.get("source_id") or resolved_user["id"]).strip().lower()
    source_name = source_cfg.get("name") or (f"@{username}" if username else resolved_user["name"])
    threshold = float(source_cfg.get("confidence_threshold", twitter_cfg.get("confidence_threshold", 0.7)))
    max_chars = int(source_cfg.get("max_post_chars", twitter_cfg.get("max_post_chars", 12000)))

    posts = await x_client.fetch_recent_posts(resolved_user["id"])
    if not posts:
        logger.info("No posts found for %s", source_name)
        return

    deduped_posts = []
    seen_ids = set()
    for p in posts:
        post_id = p.get("post_id")
        if not post_id or post_id in seen_ids:
            continue
        seen_ids.add(post_id)
        deduped_posts.append(p)

    if not has_any_posts(source_id, db_path):
        await _seed_posts(source_id, source_name, deduped_posts, db_path, bot, admin_chat_id)
        return

    new_posts = [p for p in deduped_posts if not is_processed(source_id, p["post_id"], db_path)]
    if not new_posts:
        logger.info("No new posts for %s", source_name)
        return

    logger.info("Found %d new post(s) for %s", len(new_posts), source_name)
    for post in reversed(new_posts):
        try:
            await _process_post(
                source_id=source_id,
                source_name=source_name,
                post=post,
                max_chars=max_chars,
                threshold=threshold,
                db_path=db_path,
                analyzer=analyzer,
                bot=bot,
                target_channel_id=target_channel_id,
                admin_chat_id=admin_chat_id,
                error_alerts_enabled=error_alerts_enabled,
            )
        except Exception as e:
            logger.error("Post processing failed for %s: %s", post.get("post_url"), e, exc_info=True)


async def _process_post(
    source_id: str,
    source_name: str,
    post: dict,
    max_chars: int,
    threshold: float,
    db_path: str,
    analyzer: LLMAnalyzer,
    bot,
    target_channel_id: int | None,
    admin_chat_id: int | None,
    error_alerts_enabled: bool,
):
    post_id = post["post_id"]
    post_url = post["post_url"]
    post_title = post.get("title", "Untitled X post")
    post_text = str(post.get("text") or "").strip()

    if not post_text:
        logger.warning("No text found for %s", post_url)
        mark_processed(
            source_id=source_id,
            post_id=post_id,
            post_url=post_url,
            post_title=post_title,
            published_at=post.get("published_at"),
            content_chars=0,
            skip_reason="empty_body",
            db_path=db_path,
        )
        return

    metrics = post.get("metrics") or {}
    metric_line = ""
    if metrics:
        metric_line = "\n\nPublic metrics: " + ", ".join(
            f"{k}={v}" for k, v in sorted(metrics.items()) if isinstance(v, int)
        )
    analysis_text = f"{post_text}{metric_line}"[:max_chars]

    logger.info("Analyzing '%s' (%d chars)...", post_title, len(analysis_text))
    result = await analyzer.analyze(analysis_text, post_title=post_title)
    if not result:
        attempts = bump_attempts(source_id, post_id, last_error="llm_failed", db_path=db_path)
        if attempts >= LLM_MAX_POST_ATTEMPTS:
            logger.error("Giving up on %s after %d failed attempt(s)", post_url, attempts)
            mark_processed(
                source_id=source_id,
                post_id=post_id,
                post_url=post_url,
                post_title=post_title,
                published_at=post.get("published_at"),
                content_chars=len(analysis_text),
                skip_reason=f"llm_failed_after_{attempts}_attempts",
                db_path=db_path,
            )
            await _maybe_alert(
                bot,
                admin_chat_id,
                error_alerts_enabled,
                f"LLM analysis permanently failed after {attempts} attempt(s): {source_name} — {post_url}",
            )
        else:
            logger.warning(
                "Analysis failed for %s (post_attempts=%d/%d, consecutive=%d) — will retry next scan",
                post_url,
                attempts,
                LLM_MAX_POST_ATTEMPTS,
                analyzer.consecutive_failures,
            )
            if analyzer.consecutive_failures >= 3 and analyzer.consecutive_failures % 3 == 0:
                await _maybe_alert(
                    bot,
                    admin_chat_id,
                    error_alerts_enabled,
                    f"LLM analysis failed {analyzer.consecutive_failures} consecutive time(s); latest: {source_name} — {post_url}",
                )
        return

    mark_processed(
        source_id=source_id,
        post_id=post_id,
        post_url=post_url,
        post_title=post_title,
        published_at=post.get("published_at"),
        content_chars=len(analysis_text),
        db_path=db_path,
    )

    tickers = result.get("tickers") or []
    is_signal = bool(result.get("is_signal"))
    confidence = float(result.get("confidence", 0.0))
    has_tickers = len(tickers) >= 1

    if is_signal and confidence >= threshold and has_tickers:
        logger.info("Signal (%.0f%%, %d ticker(s)): %s", confidence * 100, len(tickers), source_name)
        for ticker in tickers:
            recent_mentions = get_recent_ticker_mentions(
                ticker.get("symbol", ""),
                hours=48,
                exclude_source_id=source_id,
                db_path=db_path,
            )
            if recent_mentions:
                ticker["recent_mentions"] = recent_mentions
        telegram_urls_by_symbol = await send_signal(
            bot=bot,
            target_channel_id=target_channel_id,
            source_name=source_name,
            post_title=post_title,
            post_url=post_url,
            analysis=result,
        )
        record_signal_tickers(
            source_id=source_id,
            source_name=source_name,
            post_id=post_id,
            post_url=post_url,
            post_title=post_title,
            published_at=post.get("published_at"),
            tickers=tickers,
            telegram_message_urls_by_symbol=telegram_urls_by_symbol,
            db_path=db_path,
        )
    else:
        logger.info(
            "No signal (is_signal=%s, %.0f%%, %d ticker(s)): %s",
            is_signal,
            confidence * 100,
            len(tickers),
            source_name,
        )


async def main():
    config_path = "config.yaml"
    cfg = _load_config(config_path)

    llm_cfg = _build_llm_config(cfg, config_path)

    x_cfg = dict(cfg.get("x", {}))
    x_cfg["bearer_token"] = x_cfg.get("bearer_token") or os.getenv("X_BEARER_TOKEN") or os.getenv("TWITTER_BEARER_TOKEN") or ""
    if not x_cfg["bearer_token"]:
        logger.error("X API bearer token is missing. Set x.bearer_token or X_BEARER_TOKEN.")
        return

    db_path = os.path.expanduser(cfg.get("data", {}).get("db_path", "data/posts.db"))
    init_db(db_path)

    analyzer = LLMAnalyzer(llm_cfg)
    x_client = XAPIClient(x_cfg)

    from telegram import Bot

    bot = Bot(token=cfg["telegram"]["bot_token"])
    target_channel_id = _resolve_target_channel_id(cfg)
    admin_chat_id = cfg.get("admin", {}).get("chat_id")
    if admin_chat_id:
        admin_chat_id = int(admin_chat_id)

    twitter_cfg = cfg.get("twitter", {})
    sources = [s for s in twitter_cfg.get("sources", []) if s.get("enabled", True)]
    poll_interval = int(twitter_cfg.get("poll_interval_seconds", 900))

    if not sources:
        logger.error("No X/Twitter sources configured. Check config.yaml.")
        return

    logger.info("Twitter monitor starting: %d source(s), poll every %ds", len(sources), poll_interval)

    await analyzer.open()
    await x_client.open()
    try:
        while True:
            error_alerts_enabled = bool(cfg.get("error_alerts", {}).get("enabled", True))
            for source in sources:
                try:
                    await _scan_source(
                        x_client=x_client,
                        source_cfg=source,
                        twitter_cfg=twitter_cfg,
                        db_path=db_path,
                        analyzer=analyzer,
                        bot=bot,
                        target_channel_id=target_channel_id,
                        admin_chat_id=admin_chat_id,
                        error_alerts_enabled=error_alerts_enabled,
                    )
                except Exception as e:
                    source_name = source.get("name") or source.get("username") or source.get("user_id") or "unknown"
                    logger.error("Scan failed for %s: %r", source_name, e, exc_info=True)
                    if admin_chat_id and cfg.get("error_alerts", {}).get("enabled", True):
                        await send_error_alert(bot, admin_chat_id, f"Scan failed for {source_name}: {e}")
            logger.info("Scan complete. Sleeping %ds...", poll_interval)
            await asyncio.sleep(poll_interval)
    except Exception as e:
        if admin_chat_id and cfg.get("error_alerts", {}).get("enabled", True):
            await send_error_alert(bot, admin_chat_id, f"Fatal monitor error: {e}")
        raise
    finally:
        await analyzer.close()
        await x_client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped.")
        sys.exit(0)
