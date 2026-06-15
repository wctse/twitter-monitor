from datetime import datetime, timedelta, timezone

from db import get_recent_ticker_mentions, init_db, record_signal_tickers


def _iso(dt):
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def test_recent_ticker_mentions_prefers_telegram_url_and_excludes_current_source(tmp_path):
    db_path = str(tmp_path / "posts.db")
    init_db(db_path)
    now = datetime.now(timezone.utc)

    record_signal_tickers(
        source_id="serenity",
        source_name="Serenity",
        post_id="1",
        post_url="https://x.com/serenity/status/1",
        post_title="Post 1",
        published_at=_iso(now - timedelta(hours=1)),
        tickers=[{"symbol": "WOLF", "bias": "bullish"}],
        telegram_message_urls_by_symbol={"WOLF": "https://t.me/c/123/10"},
        db_path=db_path,
    )
    record_signal_tickers(
        source_id="current",
        source_name="Current",
        post_id="2",
        post_url="https://x.com/current/status/2",
        post_title="Post 2",
        published_at=_iso(now - timedelta(hours=2)),
        tickers=[{"symbol": "WOLF", "bias": "bullish"}],
        db_path=db_path,
    )
    record_signal_tickers(
        source_id="old",
        source_name="Old",
        post_id="3",
        post_url="https://x.com/old/status/3",
        post_title="Post 3",
        published_at=_iso(now - timedelta(hours=49)),
        tickers=[{"symbol": "WOLF", "bias": "bullish"}],
        db_path=db_path,
    )

    mentions = get_recent_ticker_mentions("wolf", exclude_source_id="current", db_path=db_path)

    assert mentions == [{"source_name": "Serenity", "url": "https://t.me/c/123/10"}]


def test_recent_ticker_mentions_falls_back_to_twitter_url(tmp_path):
    db_path = str(tmp_path / "posts.db")
    init_db(db_path)
    now = datetime.now(timezone.utc)

    record_signal_tickers(
        source_id="jukan",
        source_name="Jukan",
        post_id="1",
        post_url="https://x.com/jukan/status/1",
        post_title="Post 1",
        published_at=_iso(now),
        tickers=[{"symbol": "LITE", "bias": "bullish"}],
        db_path=db_path,
    )

    mentions = get_recent_ticker_mentions("LITE", db_path=db_path)

    assert mentions == [{"source_name": "Jukan", "url": "https://x.com/jukan/status/1"}]
