import asyncio

from notifier import TELEGRAM_MAX_MESSAGE_CHARS, _sort_tickers, render_messages, send_signal


def _make_analysis(tickers, confidence=0.85, summary="Test summary"):
    return {
        "is_signal": True,
        "confidence": confidence,
        "summary": summary,
        "tickers": tickers,
    }


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


def test_sort_tickers_puts_neutral_last():
    sorted_t = _sort_tickers(
        [
            {"symbol": "A", "bias": "neutral"},
            {"symbol": "B", "bias": "bullish"},
            {"symbol": "C", "bias": "bearish"},
            {"symbol": "D", "bias": "neutral"},
        ]
    )
    biases = [t["bias"] for t in sorted_t]
    assert biases == ["bullish", "bearish", "neutral", "neutral"]


def test_render_single_message_under_limit():
    analysis = _make_analysis(
        [
            {"symbol": "NVDA", "bias": "bullish", "thesis": "AI demand", "timeframe": "months", "price_target": "$200"},
            {"symbol": "INTC", "bias": "bearish", "thesis": "Losing share", "timeframe": "", "price_target": ""},
        ]
    )
    msgs = render_messages("@source", "Post Title", "https://x.com/source/status/1", analysis)
    assert len(msgs) == 1
    assert msgs[0].startswith("👤 <b>@source</b>")
    assert "📰" not in msgs[0]
    assert "📝" not in msgs[0]
    assert "confidence" not in msgs[0]
    assert "<b>Investment views:</b>" in msgs[0]
    assert "NVDA" in msgs[0]
    assert "INTC" in msgs[0]
    assert "⏱ months" in msgs[0]
    assert "🎯 $200" in msgs[0]


def test_render_message_splits_between_tickers_when_too_long():
    long_thesis = "x" * 1500
    analysis = _make_analysis(
        [
            {"symbol": f"T{i}", "bias": "bullish", "thesis": long_thesis, "timeframe": "", "price_target": ""}
            for i in range(6)
        ]
    )
    msgs = render_messages("@source", "Post", "https://x.com/source/status/1", analysis)
    assert len(msgs) > 1
    for msg in msgs:
        assert len(msg) <= TELEGRAM_MAX_MESSAGE_CHARS
    assert msgs[1].startswith("<b>(continued 2/")


def test_send_signal_sends_only_to_target_channel():
    bot = FakeBot()
    analysis = _make_analysis(
        [{"symbol": "NVDA", "bias": "bullish", "thesis": "AI demand", "timeframe": "", "price_target": ""}]
    )

    asyncio.run(send_signal(bot, -1003931653025, "@source", "Post", "https://x.com/source/status/1", analysis))

    assert len(bot.sent) == 1
    assert bot.sent[0]["chat_id"] == -1003931653025
    assert bot.sent[0]["parse_mode"] == "HTML"


def test_send_signal_missing_target_channel_sends_nothing():
    bot = FakeBot()
    analysis = _make_analysis(
        [{"symbol": "NVDA", "bias": "bullish", "thesis": "AI demand", "timeframe": "", "price_target": ""}]
    )

    asyncio.run(send_signal(bot, None, "@source", "Post", "https://x.com/source/status/1", analysis))

    assert bot.sent == []
