from x_client import XAPIClient, _make_title


def test_make_title_truncates_long_text():
    title = _make_title("x" * 120)
    assert len(title) == 96
    assert title.endswith("...")


def test_normalize_post_uses_cached_username_for_url():
    client = XAPIClient({"bearer_token": "test"})
    client._user_cache["id:123"] = {"id": "123", "username": "example", "name": "Example"}

    post = client._normalize_post(
        {
            "id": "999",
            "text": "Long $NVDA because AI demand is strong",
            "created_at": "2026-01-01T00:00:00.000Z",
            "public_metrics": {"like_count": 10},
        },
        "123",
    )

    assert post["post_id"] == "999"
    assert post["post_url"] == "https://x.com/example/status/999"
    assert post["title"] == "Long $NVDA because AI demand is strong"
    assert post["metrics"]["like_count"] == 10
    assert post["referenced_tweets"] == []
