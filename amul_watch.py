import os, re, json, time, random, requests
from pathlib import Path
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from datetime import datetime
import warnings, urllib3
warnings.filterwarnings("ignore", category=urllib3.exceptions.NotOpenSSLWarning)

PRODUCT_URL = "https://shop.amul.com/en/product/amul-high-protein-rose-lassi-200-ml-or-pack-of-30"
STATE_FILE = Path("state.json")
TIMEOUT = 30
ALERT_COOLDOWN_HOURS = 12

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ---------------- FETCHER ----------------
def fetch_html():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
        )
        # block heavy resources
        ctx.route("**/*", lambda route: route.abort()
                  if route.request.resource_type in {"image", "font", "media"}
                  else route.continue_())

        page = ctx.new_page()
        page.set_default_timeout(TIMEOUT * 1000)
        page.goto(PRODUCT_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        html = page.content()
        browser.close()
        return html

def fetch_html_with_retry(retries=2):
    delay = 2
    for attempt in range(retries + 1):
        try:
            return fetch_html()
        except Exception as e:
            if attempt == retries:
                raise
            time.sleep(delay + random.uniform(0, 1.5))
            delay *= 2

# ---------------- DETECTOR ----------------
def detect_status(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")

    # Prefer Add to Cart button state
    add_btn = soup.find(["button","a"], string=lambda t: t and "add to cart" in t.lower())
    if add_btn:
        if add_btn.has_attr("disabled") or "disabled" in (add_btn.get("class") or []):
            return "sold_out"
        return "available"

    # Negative signals
    if soup.find(string=lambda t: t and ("sold out" in t.lower() or "out of stock" in t.lower())):
        return "sold_out"
    if soup.find(["button","a"], string=lambda t: t and "notify me" in t.lower()):
        return "sold_out"

    # JSON-LD fallback
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "{}")
            nodes = data if isinstance(data, list) else [data]
            for n in nodes:
                offers = n.get("offers")
                if isinstance(offers, dict):
                    av = str(offers.get("availability","")).lower()
                    if "instock" in av: return "available"
                    if "outofstock" in av: return "sold_out"
        except Exception:
            pass

    return "sold_out"

# ---------------- STATE ----------------
def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"status": "unknown", "last_alert_ts": 0}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state))

def should_alert(status, prev, state):
    if status == "available" and prev != "available":
        return True
    if status == "available" and prev == "available":
        return (time.time() - state.get("last_alert_ts", 0)) >= ALERT_COOLDOWN_HOURS * 3600
    return False

# ---------------- NOTIFIERS ----------------
def notify_telegram(msg: str):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=15)
    except Exception:
        pass

def notify_email(subject: str, body: str):
    SMTP_HOST = os.getenv("SMTP_HOST")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER")
    SMTP_PASS = os.getenv("SMTP_PASS")
    EMAIL_TO  = os.getenv("EMAIL_TO")
    EMAIL_FROM= os.getenv("EMAIL_FROM", SMTP_USER)
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASS, EMAIL_TO]):
        return
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["To"] = EMAIL_TO
        msg["From"] = EMAIL_FROM
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
    except Exception:
        pass

# ---------------- MAIN ----------------
def main():
    html = fetch_html_with_retry()
    status = detect_status(html)
    state = load_state()
    prev = state.get("status")
    force = os.getenv("FORCE_ALERT") == "1"

    if should_alert(status, prev, state) or force:
        now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
        msg = f"*Amul Product Update* 🥛\nStatus: **{status.upper()}** at {now}\n{PRODUCT_URL}"
        notify_telegram(msg)
        notify_email(f"Amul product {status}", msg)
        state["last_alert_ts"] = int(time.time())

    state["status"] = status
    save_state(state)

if __name__ == "__main__":
    main()