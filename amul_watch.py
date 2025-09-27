
# --- Improved version ---
import os, re, json, time, random, requests, logging
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from datetime import datetime, timedelta, timezone

PRODUCT_URL = "https://shop.amul.com/en/product/amul-high-protein-rose-lassi-200-ml-or-pack-of-30"
STATE_FILE = Path("state.json")
TIMEOUT = 30
RETRY_LIMIT = 3
RETRY_BASE = 2
RETRY_JITTER = 2  
RE_ALERT_HOURS = 12

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

logging.basicConfig(level=logging.INFO)

def fetch_html():
    for attempt in range(RETRY_LIMIT):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context(user_agent="Mozilla/5.0")
                # Block images, fonts, media
                ctx.route("**/*", lambda route, req: route.abort() if req.resource_type in ["image", "media", "font"] else route.continue_())
                page = ctx.new_page()
                page.set_default_timeout(TIMEOUT * 1000)
                page.goto(PRODUCT_URL, wait_until="networkidle")
                page.wait_for_timeout(1500)
                html = page.content()
                browser.close()
                return html
        except Exception as e:
            wait = RETRY_BASE ** attempt + random.uniform(0, RETRY_JITTER)
            logging.warning(f"Fetch failed (attempt {attempt+1}/{RETRY_LIMIT}): {e}. Retrying in {wait:.1f}s...")
            time.sleep(wait)
    raise RuntimeError("Failed to fetch product page after retries.")

def detect_status(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    # 1. Add to Cart button state
    add_btn = soup.find(["button", "a"], string=lambda t: t and "add to cart" in t.lower())
    if add_btn:
        cls = add_btn.get("class") or []
        if add_btn.has_attr("disabled") or "disabled" in cls:
            return "sold_out"
        return "available"
    # 2. Notify Me button or Sold Out text
    if soup.find(["button", "a"], string=lambda t: t and "notify me" in t.lower()):
        return "sold_out"
    if soup.find(string=lambda t: t and "sold out" in t.lower()):
        return "sold_out"
    # 3. JSON-LD (optional)
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "{}")
            nodes = data if isinstance(data, list) else [data]
            for n in nodes:
                offers = n.get("offers")
                if isinstance(offers, dict):
                    av = str(offers.get("availability", "")).lower()
                    if "instock" in av: return "available"
                    if "outofstock" in av: return "sold_out"
        except Exception:
            pass
    return "sold_out"

def load_state():
    if STATE_FILE.exists():
        try: return json.loads(STATE_FILE.read_text())
        except Exception: pass
    return {"status": "unknown", "last_alert_ts": 0}

def save_state(state): STATE_FILE.write_text(json.dumps(state))

def notify_telegram(msg: str):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID): return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=15)
    except Exception as e:
        logging.warning(f"Telegram notify failed: {e}")

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
    except Exception as e:
        logging.warning(f"Email notify failed: {e}")

def ist_now():
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30)))

def main():
    html = fetch_html()
    status = detect_status(html)
    state = load_state()
    prev = state.get("status", "unknown")
    last_alert_ts = state.get("last_alert_ts", 0)
    now_ts = int(time.time())
    force = os.getenv("FORCE_ALERT") == "1"

    # Alert logic: only on transition, or re-alert every 12h if still available
    should_alert = False
    if (status == "available" and prev != "available") or force:
        should_alert = True
    elif status == "available" and prev == "available":
        if now_ts - last_alert_ts > RE_ALERT_HOURS * 3600:
            should_alert = True

    if should_alert:
        ist_time = ist_now().strftime('%Y-%m-%d %H:%M:%S IST')
        msg = f"""*Amul Product Available!*\n[{PRODUCT_URL}]({PRODUCT_URL})\n_Time: {ist_time}_"""
        notify_telegram(msg)
        notify_email("Amul product available", f"Available at {PRODUCT_URL} (Checked: {ist_time})")
        state["last_alert_ts"] = now_ts

    state["status"] = status
    save_state(state)

if __name__ == "__main__":
    main()