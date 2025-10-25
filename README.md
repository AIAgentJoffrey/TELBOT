[README.md](https://github.com/user-attachments/files/23136760/README.md)
# Telegram Sora Video Bot (Railway)

## Env vars

Set the following environment variables in your deployment platform:

- `TG_BOT_TOKEN` – Telegram bot token from @BotFather.
- `SORA_API_BASE` – Base URL for OpenAI Sora API (e.g. `https://api.openai.com`).
- `SORA_API_KEY` – Your OpenAI API key starting with `sk-`.

## Deployment on Railway

1. Create a new Railway project and link this repository.
2. Add the environment variables listed above in the project settings.
3. Deploy the project.

## Usage

Start the bot with `/start` in Telegram and follow the instructions. Send a line in the format
`<продукт>|<аудитория>|<тон>|<секунди>|<аспект>` followed by any asset URLs on separate lines, then `/go` to generate a video.
