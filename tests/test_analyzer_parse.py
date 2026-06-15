import pytest

from analyzer import LLMAnalyzer


def _make_analyzer():
    return LLMAnalyzer(
        {
            "provider": "api",
            "base_url": "https://example.com",
            "model": "test-model",
            "api_key": "test",
            "prompt": "you are a test prompt",
        }
    )


def test_constructor_requires_prompt():
    with pytest.raises(ValueError):
        LLMAnalyzer(
            {
                "provider": "api",
                "base_url": "https://example.com",
                "model": "test-model",
            }
        )


def test_parse_valid_response():
    raw = (
        '{"is_signal": true, "confidence": 0.85, '
        '"summary": "Long NVDA", '
        '"contributing_item_numbers": [2], '
        '"tickers": [{"symbol": "nvda", "bias": "BULLISH", "thesis": "AI demand", "timeframe": "MONTHS", "price_target": "$200", "contributing_item_numbers": [2]}]}'
    )
    result = LLMAnalyzer._parse(raw)
    assert result["is_signal"] is True
    assert result["confidence"] == 0.85
    assert result["tickers"][0]["symbol"] == "NVDA"
    assert result["tickers"][0]["bias"] == "bullish"
    assert result["tickers"][0]["timeframe"] == "months"
    assert result["tickers"][0]["price_target"] == "$200"
    assert result["contributing_item_numbers"] == [2]
    assert result["tickers"][0]["contributing_item_numbers"] == [2]


def test_parse_drops_invalid_bias_to_neutral():
    raw = '{"is_signal": false, "confidence": 0.1, "tickers": [{"symbol": "X", "bias": "yolo"}]}'
    result = LLMAnalyzer._parse(raw)
    assert result["tickers"][0]["bias"] == "neutral"


def test_parse_returns_none_for_invalid_json():
    result = LLMAnalyzer._parse("this is not json")
    assert result is None


def test_constructor_accepts_inline_prompt():
    analyzer = _make_analyzer()
    assert analyzer.prompt == "you are a test prompt"
