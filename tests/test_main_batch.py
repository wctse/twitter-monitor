import asyncio
from datetime import datetime, timezone

import main
from db import init_db, is_processed, record_signal_tickers


class FakeAnalyzer:
    def __init__(self, result):
        self.result = result
        self.calls = []
        self.consecutive_failures = 0

    async def analyze(self, post_text, post_title=""):
        self.calls.append((post_text, post_title))
        return self.result


class FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id
        self.username = None


class FakeMessage:
    def __init__(self, chat_id, message_id):
        self.chat = FakeChat(chat_id)
        self.message_id = message_id


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return FakeMessage(kwargs["chat_id"], len(self.sent))


def _post(post_id, text, referenced_tweets=None):
    return {
        "post_id": post_id,
        "post_url": f"https://x.com/source/status/{post_id}",
        "title": text,
        "text": text,
        "published_at": "2026-01-01T00:00:00Z",
        "metrics": {"like_count": int(post_id)},
        "referenced_tweets": referenced_tweets or [],
    }


def test_build_batch_analysis_includes_all_posts_and_types():
    analysis_text, title, urls = main._build_batch_analysis(
        [
            _post("1", "Long $NVDA", []),
            _post("2", "Reply adds context", [{"type": "replied_to", "id": "1"}]),
            _post("3", "RT @x", [{"type": "retweeted", "id": "9"}]),
        ],
        12000,
    )

    assert title == "3 X posts from one poll"
    assert urls == [
        "https://x.com/source/status/1",
        "https://x.com/source/status/2",
        "https://x.com/source/status/3",
    ]
    assert "Type: post" in analysis_text
    assert "Type: reply" in analysis_text
    assert "Type: retweet" in analysis_text
    assert "Determine which items are valuable" in analysis_text


def test_process_posts_analyzes_one_batch_and_links_only_contributing_posts(tmp_path):
    db_path = str(tmp_path / "posts.db")
    init_db(db_path)
    analyzer = FakeAnalyzer(
        {
            "is_signal": True,
            "confidence": 0.9,
            "summary": "Batch signal",
            "contributing_item_numbers": [2],
            "tickers": [{"symbol": "NVDA", "bias": "bullish", "thesis": "AI demand"}],
        }
    )
    bot = FakeBot()
    posts = [_post("1", "Unrelated reply"), _post("2", "More context on $NVDA")]

    asyncio.run(
        main._process_posts(
            source_id="source",
            source_name="Source",
            posts=posts,
            max_chars=12000,
            threshold=0.7,
            db_path=db_path,
            analyzer=analyzer,
            bot=bot,
            target_channel_id=-1003931653025,
            admin_chat_id=None,
            error_alerts_enabled=True,
        )
    )

    assert len(analyzer.calls) == 1
    assert "Item 1 of 2" in analyzer.calls[0][0]
    assert "Item 2 of 2" in analyzer.calls[0][0]
    assert len(bot.sent) == 1
    assert "https://x.com/source/status/1" not in bot.sent[0]["text"]
    assert "https://x.com/source/status/2" in bot.sent[0]["text"]
    assert is_processed("source", "1", db_path)
    assert is_processed("source", "2", db_path)


def test_process_posts_removes_ticker_views_repeated_by_same_author_and_direction(tmp_path):
    db_path = str(tmp_path / "posts.db")
    init_db(db_path)
    record_signal_tickers(
        source_id="source",
        source_name="Source",
        post_id="old",
        post_url="https://x.com/source/status/old",
        post_title="Old post",
        published_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        tickers=[{"symbol": "NVDA", "bias": "bullish"}],
        db_path=db_path,
    )
    analyzer = FakeAnalyzer(
        {
            "is_signal": True,
            "confidence": 0.9,
            "summary": "Batch signal",
            "tickers": [
                {"symbol": "NVDA", "bias": "bullish", "thesis": "Repeated AI demand"},
                {"symbol": "TSLA", "bias": "bearish", "thesis": "Margin pressure"},
            ],
        }
    )
    bot = FakeBot()

    asyncio.run(
        main._process_posts(
            source_id="source",
            source_name="Source",
            posts=[_post("2", "Long $NVDA but short $TSLA")],
            max_chars=12000,
            threshold=0.7,
            db_path=db_path,
            analyzer=analyzer,
            bot=bot,
            target_channel_id=-1003931653025,
            admin_chat_id=None,
            error_alerts_enabled=True,
        )
    )

    assert len(bot.sent) == 1
    assert "NVDA" not in bot.sent[0]["text"]
    assert "TSLA" in bot.sent[0]["text"]


def test_process_posts_sends_nothing_when_all_views_are_removed_by_cooldown(tmp_path):
    db_path = str(tmp_path / "posts.db")
    init_db(db_path)
    record_signal_tickers(
        source_id="source",
        source_name="Source",
        post_id="old",
        post_url="https://x.com/source/status/old",
        post_title="Old post",
        published_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        tickers=[{"symbol": "NVDA", "bias": "bullish"}],
        db_path=db_path,
    )
    analyzer = FakeAnalyzer(
        {
            "is_signal": True,
            "confidence": 0.9,
            "summary": "Batch signal",
            "tickers": [{"symbol": "NVDA", "bias": "bullish", "thesis": "Repeated AI demand"}],
        }
    )
    bot = FakeBot()

    asyncio.run(
        main._process_posts(
            source_id="source",
            source_name="Source",
            posts=[_post("2", "Long $NVDA again")],
            max_chars=12000,
            threshold=0.7,
            db_path=db_path,
            analyzer=analyzer,
            bot=bot,
            target_channel_id=-1003931653025,
            admin_chat_id=None,
            error_alerts_enabled=True,
        )
    )

    assert bot.sent == []
    assert is_processed("source", "2", db_path)


def test_contributing_item_numbers_falls_back_to_first_item():
    result = {"contributing_item_numbers": [99, "bad"], "tickers": []}

    assert main._contributing_item_numbers(result, [], 2) == [1]
