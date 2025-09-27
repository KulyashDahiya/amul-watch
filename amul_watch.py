import os, re, json, time, requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from pathlib import Path
from playwright.sync_api import sync_playwright

PRODUCT_URL = "https://shop.amul.com/en/product/amul-high-protein-rose-lassi-200-ml-or-pack-of-30"
STATE_FILE = Path("state.json")
TIMEOUT = 30

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def fetch_html():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent="Mozilla/5.0")
        page = ctx.new_page()
        page.set_default_timeout(TIMEOUT * 1000)
        page.goto(PRODUCT_URL, wait_until="networkidle")
        page.wait_for_timeout(1500)
        html = page.content()
        browser.close()
        return html

def detect_status(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")

    # explicit “Sold Out”
    if soup.find(string=lambda t: t and "sold out" in t.lower()):
        return "sold_out"

    # “Notify Me” button
    if soup.find(["button","a"], string=lambda t: t and "notify me" in t.lower()):
        return "sold_out"

    # “Add to Cart” button state
    add_btn = soup.find(["button","a"], string=lambda t: t and "add to cart" in t.lower())
    if add_btn:
        cls = add_btn.get("class") or []
        if add_btn.has_attr("disabled") or "disabled" in cls:
            return "sold_out"
        return "available"

    # JSON-LD (optional)
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            import json as _json
            data = _json.loads(s.string or "{}")
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

def load_state():
    if STATE_FILE.exists():
        try: return json.loads(STATE_FILE.read_text())
        except Exception: pass
    return {"status":"unknown"}

def save_state(state): STATE_FILE.write_text(json.dumps(state))

def notify_telegram(msg: str):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID): return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=15)
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

def main():
    html = fetch_html()
    status = detect_status(html)
    state = load_state()
    prev = state.get("status")

    # For testing alerts: set env FORCE_ALERT=1 in the workflow dispatch
    force = os.getenv("FORCE_ALERT") == "1"

    if (status == "available" and prev != "available") or force:
        msg = f"Amul product is {status.upper()} → {PRODUCT_URL}"
        notify_telegram(msg)
        notify_email(f"Amul product {status}", msg)

    state["status"] = status
    save_state(state)

if __name__ == "__main__":
    main()