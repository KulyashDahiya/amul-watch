# 🥛 Amul Stock Checker

A robust Python bot that monitors the availability of **Amul protein products** (like Rose Lassi, Whey Protein, etc.) and sends alerts via **Telegram** and/or **Email** when stock changes are detected.

Supports:
- ✅ GitHub Actions (auto-run every 5 minutes)
- ✅ Local runs (one-shot or scheduled with cron/launchd)
- ✅ State tracking (no duplicate alerts unless stock changes)

---

## ✨ Features
- Track one or more Amul products by **alias** (e.g. `amul-high-protein-rose-lassi-200-ml-or-pack-of-30`)
- Reliable **API-based stock checking**
- **Email + Telegram** notifications
- Works with your **local `.env`** or **GitHub secrets**
- Stores last state in `state.json` (ignored by Git)

---

## 🚀 Setup

1. **Clone this repo**
2. **Install dependencies locally (optional for local testing):**
	```sh
	pip install -r requirements.txt
	python -m playwright install chromium
	```
3. **Create a Telegram bot** with [@BotFather](https://t.me/BotFather) and get your bot token and chat ID.
4. **(Optional) Set up email SMTP credentials** for email alerts.
5. **Copy `.env` to `.env` and fill in your secrets:**
	```env
	TELEGRAM_BOT_TOKEN=your_bot_token
	TELEGRAM_CHAT_ID=your_chat_id
	SMTP_HOST=smtp.gmail.com
	SMTP_PORT=587
	SMTP_USER=your_email@gmail.com
	SMTP_PASS=your_app_password
	EMAIL_TO=your_email@gmail.com
	EMAIL_FROM=your_email@gmail.com
	```
6. **Do NOT commit your real `.env` or `state.json` files.**

## GitHub Actions (Cloud, 24x7)

1. Push your code to GitHub.
2. Go to your repo → Settings → Secrets and variables → Actions, and add:
	- `TELEGRAM_BOT_TOKEN`
	- `TELEGRAM_CHAT_ID`
	- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `EMAIL_TO`, `EMAIL_FROM` (for email)
3. The workflow in `.github/workflows/amul_watch.yml` will run every 5 minutes and send alerts when the product is available.

## Local Testing (optional)

You can run the script locally:
```sh
python amul_watch.py
```

## Notes
- The script uses Playwright to handle dynamic content on the Amul site.
- Alerts are only sent when the product status changes to available.
- For 24×7 monitoring, GitHub Actions is recommended.

---
MIT License