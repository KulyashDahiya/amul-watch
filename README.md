AMUL-WATCH

Watch selected Amul shop product pages and notify when an item becomes available.
Runs on a GitHub Actions cron every 5 minutes (and can be started manually). Alerts can be sent to Telegram and/or Email. State is persisted between runs so you only get alerted on changes.

How it works

amul_watch.py bootstraps a browsing session, sets the Amul substore preference, and calls Amul’s product API for the aliases you care about.

It writes a compact snapshot to state.json to remember the last known available, inventory_quantity, and price.

On each run it compares the latest snapshot with the previous one and notifies only when availability flips to True (unless FORCE_ALERT=1).

The GitHub workflow:

Caches a Python venv keyed by the OS, Python version, and requirements.txt hash so dependencies are skipped on cache hits.

Restores/saves state.json so alerts are change-only across runs.

Publishes a brief job summary.

Repository layout
.github/workflows/amul_watch.yml   # CI workflow (cron + manual dispatch)
amul_watch.py                      # Watcher script
requirements.txt                   # Python deps
.env                               # (optional) Local testing env vars
state.json                         # Persistent state (created at first run)
README.md                          # This file

Prerequisites

Python 3.11 (locally; Actions sets this up automatically).

A Telegram bot & chat (optional) if you want Telegram alerts.

An SMTP account (optional) if you want email alerts.

Quick start (local)

Create and activate a venv

python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -r requirements.txt


Configure environment variables
Create a .env file (used only for local runs):

# Telegram (optional)
TELEGRAM_BOT_TOKEN=123456:abc...
TELEGRAM_CHAT_ID=123456789

# Email (optional)
EMAIL_FROM=alerts@example.com
EMAIL_TO=you@example.com
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=alerts@example.com
SMTP_PASS=yourpassword

# Force a one-time alert regardless of change (useful for testing)
FORCE_ALERT=0


Choose products to track
Edit TARGET_ALIASES at the top of amul_watch.py. Example (default):

TARGET_ALIASES = [
    "amul-high-protein-rose-lassi-200-ml-or-pack-of-30",
    # "amul-high-protein-blueberry-shake-200-ml-or-pack-of-30",
]


Run it

python amul_watch.py


A state.json file will be created on first run.

If you set up Telegram and/or Email, you’ll get a message when an item becomes available (or immediately if FORCE_ALERT=1).

GitHub Actions setup (recommended)

This repo already contains .github/workflows/amul_watch.yml. The workflow:

Runs every 5 minutes (*/5 * * * *).

Can be started manually from the Actions tab.

Uses concurrency to cancel overlapping runs.

Caches the virtualenv and state.json.

1) Add repository secrets

Go to Settings → Secrets and variables → Actions → New repository secret and add any you use:

TELEGRAM_BOT_TOKEN

TELEGRAM_CHAT_ID

EMAIL_FROM

EMAIL_TO

SMTP_HOST

SMTP_PORT (e.g., 587)

SMTP_USER

SMTP_PASS

You can use only Telegram, only Email, both, or neither. If a channel’s vars are missing, it’s skipped.

2) (Optional) Add repository variables

FORCE_ALERT → set to 1 to force a one-time alert on the next run (then set back to 0).

3) Push and watch it run

Every run appends a short timestamped summary to the job summary panel. Alerts trigger only on availability changes (unless forced).

Configuration details
Substore / region

The script currently targets:

SUBSTORE_ID = "66505ff8c8f2d6e221b9180c" (UP/NCR)

Preference is set to up-ncr during session boot.

If your pincode/region differs, adjust set_preference_substore("up-ncr") and/or the constants near the top of the script.

What counts as an alert?

Only this transition:

available: False/None → True ✅ alert

True → True or True/False → False ❌ no alert

Use FORCE_ALERT=1 (env var or repo variable) to send an alert regardless of change—handy for wiring tests.

State file (state.json)

Stores last snapshot per alias under tracked.

Stores a history list with minimal context for alerted events.

In Actions, it’s restored/saved every run so the bot remembers past states.

Caching

Venv cache: the entire .venv/ folder is cached with key
venv-${OS}-${python-version}-${hash(requirements.txt)}.
When requirements.txt or Python/OS changes, the key changes and the cache is rebuilt once.

State cache: state.json is restored before the run and saved after the run with a constant key (amul-state) so state persists across jobs.

Troubleshooting

No alerts coming through

Confirm the product alias is correct and present in the API response.

Make sure the selected substore actually carries the product.

Verify Telegram/Email secrets are set; check workflow logs for “missing … envs; skipped”.

Frequent notices but no alerts

That’s expected when availability hasn’t changed. Set FORCE_ALERT=1 once to test the pipeline.

HTTP errors / session init failed

The script uses retries with backoff. Transient failures are logged as ::notice and will retry on the next run.

Change region

Update set_preference_substore("up-ncr"), SUBSTORE_ID, or both. Run locally with FORCE_ALERT=1 to verify.

Development notes

Logged at INFO by default.

Uses a small rotating User-Agent pool.

Keeps the request headers close to what the site expects and computes a tid header per request based on session info.

Timeouts & retries are configurable via constants (TIMEOUT_*, RETRIES_*).

Security & ethics

Keep your secrets in GitHub Actions Secrets, not in code or .env committed to the repo.

This project calls public endpoints exposed by the Amul shop. Use responsibly and respect their terms of service. Consider increasing the cron interval if you don’t need 5-minute granularity.

License

MIT — do what you want, no warranty. If you improve it (new regions, nicer alerts, better deduping), PRs are welcome!
