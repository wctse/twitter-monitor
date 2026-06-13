# Twitter Monitor

Polls configured X/Twitter accounts through the official X API, analyzes new posts for actionable trading signals, and forwards high-confidence signals to a Telegram channel using the same message format as the existing monitor repos.

## How it works

1. Poll each configured account on an interval
2. Resolve usernames to X user IDs through API v2
3. Fetch recent posts from `/2/users/:id/tweets`
4. Seed existing posts on first run to avoid historical floods
5. Analyze only new posts with the configured LLM
6. Forward to Telegram only when `is_signal == true`, confidence meets threshold, and at least one ticker is present

## Setup

```bash
pip install -r requirements.txt
cp config.yaml.example config.yaml
```

Set these values in `config.yaml`:

- `x.bearer_token` or environment variable `X_BEARER_TOKEN`
- `llm.api_key`
- `telegram.bot_token`
- `telegram.target_channel_id`
- `twitter.sources`

## Example source

```yaml
twitter:
  sources:
    - name: "Example Account"
      username: "example"
      enabled: true
      confidence_threshold: 0.7
```

You can also use `user_id` instead of `username`.

## Run

```bash
python main.py
```

## Tests

```bash
python -m pytest tests/ -q
```

## Files

- `main.py` — entrypoint and scan loop
- `x_client.py` — official X API v2 client
- `analyzer.py` — OpenAI-compatible/Ollama LLM analyzer
- `notifier.py` — Telegram formatting and delivery
- `db.py` — SQLite dedupe and retry tracking
- `config.yaml.example` — config template
- `prompt.yaml.example` — default signal extraction prompt
