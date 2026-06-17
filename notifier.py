import logging
from html import escape


logger = logging.getLogger(__name__)


TELEGRAM_MAX_MESSAGE_CHARS = 4096
_BIAS_ICON = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}
_BIAS_ORDER = {"bullish": 0, "bearish": 1, "neutral": 2}
_TIMEFRAME_ICON = {"intraday": "⚡", "minutes": "⚡", "hours": "⏱", "days": "📅", "weeks": "🗓", "months": "🗓", "quarters": "🗓", "years": "🗓"}
_ADMIN_ONLY_PREFIX = "🔒 <b>[ADMIN ONLY]</b>\n"


def _bias_icon(bias: str) -> str:
    return _BIAS_ICON.get(bias.lower(), "⚪")


def _timeframe_icon(timeframe: str) -> str:
    return _TIMEFRAME_ICON.get(timeframe.lower(), "❔")


def prefix_admin_only_message(message: str) -> str:
    return f"{_ADMIN_ONLY_PREFIX}{message}"


def _sort_tickers(tickers: list[dict]) -> list[dict]:
    return sorted(tickers, key=lambda t: _BIAS_ORDER.get(str(t.get("bias", "neutral")).lower(), 3))


def _ticker_symbol(ticker: dict) -> str:
    return str(ticker.get("symbol", "") or "").strip().upper()


def _format_recent_mentions(mentions: list[dict]) -> str:
    links = []
    for mention in mentions:
        name = escape(str(mention.get("source_name", "") or "").strip())
        url = escape(str(mention.get("url", "") or "").strip(), quote=True)
        if name and url:
            links.append(f'<a href="{url}">{name}</a>')
    if not links:
        return ""
    return f"<i>Recent mentions - {' | '.join(links)}</i>"


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
    line = f"{icon} <b>{symbol}</b> — {thesis}{suffix}"
    recent_mentions = _format_recent_mentions(list(t.get("recent_mentions", []) or []))
    if recent_mentions:
        line = f"{line}\n{recent_mentions}"
    return line


def _format_post_links(post_url: str | list[str]) -> str:
    if isinstance(post_url, (list, tuple)):
        urls = [escape(str(url or "").strip(), quote=True) for url in post_url if str(url or "").strip()]
    else:
        urls = [escape(str(post_url or "").strip(), quote=True)] if str(post_url or "").strip() else []
    if not urls:
        return "🔗"
    return "\n".join(f"🔗 {url}" for url in urls)


def _format_title(source_name: str, tickers: list[dict]) -> str:
    ticker_parts = []
    for ticker in tickers:
        symbol = _ticker_symbol(ticker)
        if symbol:
            ticker_parts.append(f"{_bias_icon(str(ticker.get('bias', 'neutral')))} {escape(symbol)}")
    ticker_summary = " ".join(ticker_parts[:3]) if ticker_parts else "—"
    if len(ticker_parts) > 3:
        ticker_summary = f"{ticker_summary} +{len(ticker_parts) - 3}"
    timeframe = next((str(t.get("timeframe", "") or "").strip() for t in tickers if str(t.get("timeframe", "") or "").strip()), "unspecified")
    return f"💡 {ticker_summary} · {_timeframe_icon(timeframe)} {escape(timeframe)} — {escape(source_name)}"


def _build_header(source_name: str, post_url: str | list[str], summary: str, tickers: list[dict]) -> str:
    return (
        f"{_format_title(source_name, tickers)}\n\n"
        f"{_format_post_links(post_url)}\n\n"
        f"<b>Summary:</b> {escape(summary)}\n\n"
        f"<b>Investment views:</b>"
    )


def _render_message_chunks(
    source_name: str,
    post_title: str,
    post_url: str | list[str],
    analysis: dict,
) -> list[dict]:
    summary = str(analysis.get("summary", ""))
    tickers = _sort_tickers(list(analysis.get("tickers", [])))

    header = _build_header(source_name, post_url, summary, tickers)

    if not tickers:
        return [{"text": f"{header}\n  (no specific tickers)", "symbols": []}]

    ticker_entries = []
    for ticker in tickers:
        symbol = _ticker_symbol(ticker)
        ticker_entries.append({"text": _format_ticker_line(ticker), "symbols": [symbol] if symbol else []})

    chunks: list[dict] = []
    current = header
    current_symbols: list[str] = []
    for entry in ticker_entries:
        line = entry["text"]
        candidate = f"{current}\n\n{line}"
        if len(candidate) <= TELEGRAM_MAX_MESSAGE_CHARS - 32:
            current = candidate
            current_symbols.extend(entry["symbols"])
            continue
        if current == header:
            allowance = TELEGRAM_MAX_MESSAGE_CHARS - len(header) - 4
            truncated = line[: max(0, allowance - 3)] + "..."
            chunks.append({"text": f"{header}\n\n{truncated}", "symbols": entry["symbols"]})
            current = header
            current_symbols = []
            continue
        chunks.append({"text": current, "symbols": current_symbols})
        current = f"{header}\n\n{line}"
        current_symbols = list(entry["symbols"])
    if current and current != header:
        chunks.append({"text": current, "symbols": current_symbols})
    elif not chunks:
        chunks.append({"text": header, "symbols": []})

    if len(chunks) > 1:
        total = len(chunks)
        chunks = [
            chunk if i == 0 else {**chunk, "text": f"<b>(continued {i + 1}/{total})</b>\n\n{chunk['text']}"}
            for i, chunk in enumerate(chunks)
        ]
    return chunks


def render_messages(
    source_name: str,
    post_title: str,
    post_url: str | list[str],
    analysis: dict,
) -> list[str]:
    return [chunk["text"] for chunk in _render_message_chunks(source_name, post_title, post_url, analysis)]


def render_message(
    source_name: str,
    post_title: str,
    post_url: str | list[str],
    analysis: dict,
) -> str:
    return render_messages(source_name, post_title, post_url, analysis)[0]


def _telegram_message_url(message) -> str | None:
    if message is None:
        return None
    message_id = getattr(message, "message_id", None)
    chat = getattr(message, "chat", None)
    if not message_id or chat is None:
        return None
    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{str(username).lstrip('@')}/{message_id}"
    chat_id = getattr(chat, "id", None)
    chat_id_text = str(chat_id or "")
    if chat_id_text.startswith("-100") and len(chat_id_text) > 4:
        return f"https://t.me/c/{chat_id_text[4:]}/{message_id}"
    return None


async def send_signal(
    bot,
    target_channel_id: int | None,
    source_name: str,
    post_title: str,
    post_url: str | list[str],
    analysis: dict,
) -> dict[str, str | None]:
    if target_channel_id is None:
        logger.warning("No target channel configured — signal not sent")
        return {}

    chunks = _render_message_chunks(source_name, post_title, post_url, analysis)
    telegram_urls_by_symbol: dict[str, str | None] = {}

    for chunk in chunks:
        try:
            message = await bot.send_message(chat_id=target_channel_id, text=chunk["text"], parse_mode="HTML")
            message_url = _telegram_message_url(message)
            for symbol in chunk["symbols"]:
                telegram_urls_by_symbol.setdefault(symbol, message_url)
        except Exception as e:
            logger.error("Failed to send to target_channel_id=%d: %s", target_channel_id, e)
            break
    logger.info("Sent %d message(s) to target_channel_id=%d", len(chunks), target_channel_id)
    return telegram_urls_by_symbol


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
