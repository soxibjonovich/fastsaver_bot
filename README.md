# fastsaver_bot

A Telegram bot that downloads Instagram and TikTok media. You send it a link and it replies with the video, photo or album.

## Demo

<!-- TODO: add a screenshot or GIF -->

## Features

- Reply to a text message containing a link by looking it up with an external media API (the fastsaver.io API by default, set by `API_URL`).
- Sends videos, photos and albums. Albums are split into groups of up to 10 items.
- Delivery tries to let Telegram fetch the file from its URL first. If that fails, the bot downloads the file and uploads it itself.
- Retries once without the thumbnail if Telegram rejects it, and once more on network errors.
- Shows an "uploading..." chat action while it works.
- Media types are handled with a small Strategy pattern (`VideoStrategy`, `PhotoStrategy`).
- Bot messages are in Uzbek. Uses long polling.

Not done yet: `ALLOWED_USERS` and `ADMIN` must be set in the config, but the handlers do not use them yet, so the bot does not currently restrict who can use it. There are no tests.

## Tech stack

- Python 3.13, managed with [uv](https://docs.astral.sh/uv/)
- aiogram 3
- httpx2 (async HTTP client)
- Pydantic and pydantic-settings, python-dotenv
- Docker

## How to run

You need a bot token from [@BotFather](https://t.me/BotFather) and an API key for the media API.

Create a `.env` file in the project root:

```env
BOT_TOKEN=your-bot-token
API_KEY=your-api-key
API_URL=https://api.fastsaver.io/v1
ALLOWED_USERS=[123456789]
ADMIN=123456789
```

`API_URL` is optional (that is the default). `ALLOWED_USERS` is a JSON list of Telegram user IDs.

```bash
uv sync
uv run python run.py
```

With Docker (the `.env` file is not copied into the image, so pass it at run time):

```bash
docker build -t fastsaver-bot .
docker run --env-file .env fastsaver-bot
```

## Project structure

```
run.py                 entry point
config.py              settings from environment / .env
bot/__init__.py        bot and dispatcher setup
bot/routers/main.py    handlers, sending logic, retries
bot/misc/              API client and response schemas
```

## What I learned

<!-- TODO: write 2–4 sentences in your own words -->
