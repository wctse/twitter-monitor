import logging
from html import escape


logger = logging.getLogger(__name__)


TELEGRAM_MAX_MESSAGE_CHARS = 4096
_BIAS_ICON = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}
_BIAS_ORDER = {"bullish": 0, "bearish": 1, "neutral": 2}
_ADMIN_ONLY_PREFIX = "🔒 <b>[ADMIN ONLY]</b>\n"


def _bias_icon(bias: str) -> str:
    return _BIAS_ICON.get(bias.lower(), "⚪")


def prefix_admin_only_message(message: str) -> str:
    return f"{_ADMIN_ONLY_PREFIX}{message}"


def _sort_tickers(tickers: list[dict]) -> list[dict]:
    return sorted(tickers, key=lambda t: _BIAS_ORDER.get(str(t.get("bias", "neutral")).lower(), 3))


def _format_ticker_line(t: dict) -> str:
    icon = _bias_icon(str(t.get("bias", "neutral")))
    symbol = escape(str(t.get("symbol", "")))
    thesis = escape(str(t.get("thesis", "")))
    suffix_parts = []
    timeframe = str(t.get("timeframe", "") or "").strip()
    price_target = str(t.get("price_target", "") or "").strip()
    if timeframe:
        suffix_parts.append(f"⏱ {escape(timeframe)}")
    if price_target:
        suffix_parts.append(f"🎯 {escape(price_target)}")
    suffix = f"  ({' · '.join(suffix_parts)})" if suffix_parts else ""
    return f"{icon} <b>{symbol}</b> — {thesis}{suffix}"


def _build_header(source_name: str, post_title: str, post_url: str, summary: str, confidence: float) -> str:
    return (
        f"📰 <b>{escape(source_name)}</b>\n"
        f"📝 {escape(post_title)}\n"
        f"🔗 {post_url}\n\n"
        f"<b>Summary:</b> {escape(summary)}\n\n"
        f"<b>Investment views ({confidence:.0%} confidence):</b>"
    )


def render_messages(
    source_name: str,
    post_title: str,
    post_url: str,
    analysis: dict,
) -> list[str]:
    confidence = float(analysis.get("confidence", 0.0))
    summary = str(analysis.get("summary", ""))
    tickers = _sort_tickers(list(analysis.get("tickers", [])))

    header = _build_header(source_name, post_title, post_url, summary, confidence)

    if not tickers:
        return [f"{header}\n  (no specific tickers)"]

    ticker_lines = [_format_ticker_line(t) for t in tickers]
    messages: list[str] = []
    current = header
    for line in ticker_lines:
        candidate = f"{current}\n\n{line}"
        if len(candidate) <= TELEGRAM_MAX_MESSAGE_CHARS - 32:
            current = candidate
            continue
        if current == header:
            allowance = TELEGRAM_MAX_MESSAGE_CHARS - len(header) - 4
            truncated = line[: max(0, allowance - 3)] + "..."
            messages.append(f"{header}\n\n{truncated}")
            current = header
            continue
        messages.append(current)
        current = f"{header}\n\n{line}"
    if current and current != header:
        messages.append(current)
    elif not messages:
        messages.append(header)

    if len(messages) > 1:
        total = len(messages)
        messages = [
            msg if i == 0 else f"<b>(continued {i + 1}/{total})</b>\n\n{msg}"
            for i, msg in enumerate(messages)
        ]
    return messages


def render_message(
    source_name: str,
    post_title: str,
    post_url: str,
    analysis: dict,
) -> str:
    return render_messages(source_name, post_title, post_url, analysis)[0]


async def send_signal(
    bot,
    target_channel_id: int | None,
    source_name: str,
    post_title: str,
    post_url: str,
    analysis: dict,
):
    if target_channel_id is None:
        logger.warning("No target channel configured — signal not sent")
        return

    messages = render_messages(source_name, post_title, post_url, analysis)

    for msg in messages:
        try:
            await bot.send_message(chat_id=target_channel_id, text=msg, parse_mode="HTML")
        except Exception as e:
            logger.error("Failed to send to target_channel_id=%d: %s", target_channel_id, e)
            break
    logger.info("Sent %d message(s) to target_channel_id=%d", len(messages), target_channel_id)


async def send_seed_report(
    bot,
    admin_chat_id: int,
    source_name: str,
    post_urls: list[str],
):
    lines = [f"🌱 <b>First scan: {escape(source_name)}</b>"]
    lines.append(f"Seeded {len(post_urls)} existing post(s) — these will not be analyzed.\n")
    for url in post_urls[:20]:
        lines.append(f"• {url}")
    if len(post_urls) > 20:
        lines.append(f"... and {len(post_urls) - 20} more")
    lines.append("\nMonitoring is now active. New posts will be analyzed and forwarded.")

    message = prefix_admin_only_message("\n".join(lines))
    try:
        await bot.send_message(chat_id=admin_chat_id, text=message, parse_mode="HTML")
        logger.info("Sent seed report to admin chat_id=%d", admin_chat_id)
    except Exception as e:
        logger.error("Failed to send seed report to admin: %s", e)


async def send_error_alert(
    bot,
    admin_chat_id: int,
    error_message: str,
):
    message = prefix_admin_only_message(
        "⚠️ <b>Twitter monitor error</b>\n\n"
        f"<pre>{escape(_truncate(error_message, 3000))}</pre>"
    )
    try:
        await bot.send_message(chat_id=admin_chat_id, text=message, parse_mode="HTML")
    except Exception:
        pass


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."
