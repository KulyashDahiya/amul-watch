#!/usr/bin/env python3
import os
import sys
import json
import time
import random
import logging
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timezone
from urllib.parse import urlencode, quote_plus

import argparse
import requests
import smtplib
from email.mime.text import MIMEText

# Load .env locally; in Actions we pass env directly
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ======================================
# Config (env + constants)
# ======================================

# Target product aliases (edit as you like)
TARGET_ALIASES: List[str] = [
    # Lassi/Shakes
    "amul-high-protein-rose-lassi-200-ml-or-pack-of-30",
    # "amul-high-protein-blueberry-shake-200-ml-or-pack-of-30",
]

# Repo / workflow inputs
FORCE_ALERT = os.getenv("FORCE_ALERT", "0").strip().lower() in ("1", "true", "yes")
RUN_CONTEXT = os.getenv("RUN_ENV") or ("GithubAction" if os.getenv("GITHUB_ACTIONS") == "true" else "Local")

# PIN handling: PIN_SELECTED (preferred) -> first of PINCODES -> PINCODE
def _pick_pin_from_env() -> Optional[str]:
    pin = (os.getenv("PIN_SELECTED") or "").strip()
    if pin:
        return pin
    pins_env = (os.getenv("PINCODES") or "").strip()
    if pins_env:
        # comma/space tolerant
        pins = [p.strip() for p in pins_env.replace(";", ",").replace(" ", ",").split(",") if p.strip()]
        if pins:
            return pins[0]
    pin = (os.getenv("PINCODE") or "").strip()
    return pin or None

PINCODE_IN_USE = _pick_pin_from_env()

# Optional store overrides (set these as vars/secrets if you know them)
SUBSTORE_ALIAS_OVERRIDE = (os.getenv("SUBSTORE_ALIAS") or "").strip()  # e.g., "up-ncr"
SUBSTORE_ID_OVERRIDE    = (os.getenv("SUBSTORE_ID") or "").strip()     # e.g., "66505ff8c8f2d6e221b9180c"

# Notifications (optional)
EMAIL_FROM = os.getenv("EMAIL_FROM", "").strip()
EMAIL_TO   = os.getenv("EMAIL_TO", "").strip()
SMTP_HOST  = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT  = int(os.getenv("SMTP_PORT", "587").strip() or "587")
SMTP_USER  = os.getenv("SMTP_USER", "").strip()
SMTP_PASS  = os.getenv("SMTP_PASS", "").strip()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Files
STATE_FILE = Path("state.json")

# Amul endpoints / fields
AMUL_API_BASE = "https://shop.amul.com/api/1/entity/ms.products"
STORE_ID      = "62fa94df8c13af2e242eba16"  # main store id (stable)

FIELDS = [
    "name","brand","categories","collections","alias","sku","price","compare_price",
    "original_price","images","metafields","discounts","catalog_only","is_catalog",
    "seller","available","inventory_quantity","net_quantity","num_reviews","avg_rating",
    "inventory_low_stock_quantity","inventory_allow_out_of_stock","default_variant",
    "variants","lp_seller_ids","list_price","our_price","entity_type","inventory_management",
    "linked_product_id","seller_id","inventory_management_level"
]

# Timeouts / retries
TIMEOUT_PAGE = 60
TIMEOUT_API  = 45
RETRIES_INIT = 4
RETRIES_API  = 4

# Logging
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("amul-watch")

log.info("Current Time IST: " + datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S"))
log.info(f"Starting Amul Watch in {RUN_CONTEXT} mode; PIN={PINCODE_IN_USE or 'n/a'}")

# ======================================
# Helpers
# ======================================
def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

_UA_POOL = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
]
def rand_ua() -> str:
    return random.choice(_UA_POOL)

def default_headers() -> Dict[str, str]:
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "priority": "u=1, i",
        "referer": "https://shop.amul.com/",
        "origin": "https://shop.amul.com",
        "sec-ch-ua": "\"Google Chrome\";v=\"137\", \"Chromium\";v=\"137\", \"Not/A)Brand\";v=\"24\"",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "\"Linux\"",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "sec-gpc": "1",
        "user-agent": rand_ua(),
        "connection": "close",
        "frontend": "1",
        "base_url": "https://shop.amul.com/en/browse/protein",
    }

def backoff_sleep(attempt: int, base: float = 1.0, cap: float = 12.0):
    delay = min(cap, base * (2 ** (attempt - 1))) + random.uniform(0, 0.75)
    time.sleep(delay)

def with_retries(fn: Callable[[], Any], tries: int, label: str) -> Any:
    last_err = None
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as e:
            last_err = e
            log.warning(f"[{label}] attempt {attempt} failed: {e}")
            if attempt < tries:
                backoff_sleep(attempt)
    raise last_err  # type: ignore

def sanitize_for_telegram_html(text: str) -> str:
    return text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")

def product_url(alias: str) -> str:
    return f"https://shop.amul.com/en/product/{alias}"

# ======================================
# PIN → substore alias heuristics
# ======================================
def guess_substore_alias(pin: Optional[str]) -> str:
    """
    Minimal heuristic:
      - 110xxx (Delhi) → up-ncr
      - 12x/13x (Haryana) → up-ncr (safe default for NCR proximity)
      - 20–28xxxx (UP regions around NCR) → up-ncr
      - else fallback to up-ncr
    You can expand this as you learn more substore aliases.
    """
    if not pin or len(pin) < 3:
        return "up-ncr"
    p3 = pin[:3]
    p2 = pin[:2]
    if p3.startswith("110"):
        return "up-ncr"
    if p2 in {"12", "13"}:
        return "up-ncr"
    if p2 in {str(n) for n in range(20, 29)}:
        return "up-ncr"
    return "up-ncr"

# ======================================
# Session bootstrap (cookies + tid + preference)
# ======================================
class AmulSession:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(default_headers())
        self.tid_session: Optional[str] = None

    def _calc_tid_header(self) -> str:
        timestamp = str(int(time.time() * 1000))
        rand = str(random.randint(0, 999))
        session_id = self.tid_session or ""
        payload = f"{STORE_ID}:{timestamp}:{rand}:{session_id}".encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        return f"{timestamp}:{rand}:{digest}"

    def init_cookies(self):
        with_retries(
            lambda: self.s.get("https://shop.amul.com/en/browse/protein", timeout=TIMEOUT_PAGE),
            tries=RETRIES_INIT,
            label="init:page"
        )

        def _info():
            h = default_headers()
            r = self.s.get(
                f"https://shop.amul.com/user/info.js?_v={int(time.time()*1000)}",
                headers=h,
                timeout=TIMEOUT_API
            )
            r.raise_for_status()
            txt = r.text.strip()
            if not txt.startswith("session = "):
                raise RuntimeError("unexpected info.js body")
            obj = json.loads(txt.replace("session = ", "", 1))
            self.tid_session = obj.get("tid")
            if not self.tid_session:
                raise RuntimeError("no tid in user info")
            log.info("Got session tid.")
        with_retries(_info, tries=RETRIES_INIT, label="init:info")

    def set_preference_substore(self, pin: Optional[str] = None):
        # decide alias
        alias = SUBSTORE_ALIAS_OVERRIDE or guess_substore_alias(pin)

        def _setpref():
            h = default_headers()
            h["tid"] = self._calc_tid_header()
            r = self.s.put(
                "https://shop.amul.com/entity/ms.settings/_/setPreferences",
                json={"data": {"store": alias}},
                headers=h,
                timeout=TIMEOUT_API
            )
            if r.status_code not in (200, 204):
                raise RuntimeError(f"setPreferences {r.status_code}: {r.text[:200]}")
        with_retries(_setpref, tries=RETRIES_INIT, label="init:pref")
        log.info(f"Preference set for PIN {pin or 'n/a'} (substore alias: {alias}).")

# ======================================
# Fetch products by aliases
# ======================================
def build_api_url(aliases: List[str]) -> str:
    params = []
    for f in FIELDS:
        params.append((f"fields[{f}]", "1"))
    params += [
        ("filters[0][field]", "alias"),
        ("filters[0][operator]", "in"),
        ("filters[0][original]", "1"),
    ]
    for i, a in enumerate(aliases):
        params.append((f"filters[0][value][{i}]", a))
    params += [
        ("facets", "true"),
        ("facetgroup", "default_category_facet"),
        ("total", "1"),
        ("start", "0"),
        ("cdc", "1m"),
        ("limit", str(max(32, len(aliases) + 8))),
        ("_", str(int(time.time()))),
    ]
    # Include substore only if caller provided a known ID
    if SUBSTORE_ID_OVERRIDE:
        params.append(("substore", SUBSTORE_ID_OVERRIDE))
    return f"{AMUL_API_BASE}?{urlencode(params, doseq=True, quote_via=quote_plus)}"

def fetch_by_aliases(session: AmulSession, aliases: List[str]) -> Dict[str, Any]:
    if not aliases:
        return {"messages": [], "data": [], "paging": {"count": 0, "total": 0}}

    def _do(url: str):
        h = default_headers()
        h["tid"] = session._calc_tid_header()
        r = session.s.get(url, headers=h, timeout=TIMEOUT_API)
        if r.status_code != 200:
            raise RuntimeError(f"{r.status_code} {r.reason}")
        return r.json()

    url = build_api_url(aliases)
    try:
        return with_retries(lambda: _do(url), tries=RETRIES_API, label="api:combined")
    except Exception as last_err:
        log.info("Combined request failed; falling back to per-alias requests…")
        bucket: List[Dict[str, Any]] = []
        any_success = False
        for a in aliases:
            per_url = build_api_url([a])
            try:
                data = with_retries(lambda: _do(per_url), tries=RETRIES_API, label=f"api:{a}")
                bucket.extend(data.get("data") or [])
                any_success = True
            except Exception as e:
                log.warning(f"[{a}] per-alias failed: {e}")
        if any_success:
            return {
                "messages": [{"name": "ms.entity.products.list", "level": "success"}],
                "fileBaseUrl": "https://shop.amul.com/s/62fa94df8c13af2e242eba16/",
                "data": bucket,
                "paging": {"limit": len(aliases), "start": 0, "count": len(bucket), "total": len(bucket)},
            }
        raise RuntimeError(f"All fetch attempts failed. Last error: {last_err}")

# ======================================
# State helpers
# ======================================
def ensure_state_dir() -> Path:
    d = STATE_FILE.parent
    d.mkdir(parents=True, exist_ok=True)
    return d

def load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"Could not read state {STATE_FILE}: {e}")
    return {}

def save_state(state: Dict[str, Any]) -> None:
    try:
        ensure_state_dir()
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception as e:
        log.warning(f"Could not write state {STATE_FILE}: {e}")

# ======================================
# Availability-only alerting
# ======================================
def summarize_item(p: Dict[str, Any]) -> str:
    alias = p.get("alias")
    name = p.get("name")
    price = p.get("our_price") or p.get("price")
    inv = p.get("inventory_quantity")
    avail = p.get("available")
    return f"- {name} | alias: {alias} | price: {price} | inventory: {inv} | available: {avail}"

def should_alert_availability(prev: Dict[str, Any], cur: Dict[str, Any]) -> bool:
    prev_av = prev.get("available")
    cur_av = cur.get("available")
    return bool(cur_av) and not bool(prev_av)

# ======================================
# Notifiers (optional)
# ======================================
def send_email(subject: str, body: str) -> Optional[str]:
    if not (EMAIL_FROM and EMAIL_TO and SMTP_HOST and SMTP_USER and SMTP_PASS):
        return "email: missing SMTP envs; skipped"
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO
        msg["Subject"] = subject
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
        return None
    except Exception as e:
        return f"email error: {e}"

def send_telegram(text_html: str) -> Optional[str]:
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return "telegram: missing bot envs; skipped"
    try:
        safe_html = sanitize_for_telegram_html(text_html)
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": safe_html, "parse_mode": "HTML", "disable_web_page_preview": True}
        r = requests.post(url, json=payload, timeout=20)
        if r.status_code != 200:
            return f"telegram {r.status_code}: {r.text[:200]}"
        return None
    except Exception as e:
        return f"telegram error: {e}"

# ======================================
# Main
# ======================================
def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--geojson", help="Optional path to a selected PIN geojson (not required for API)", default=None)
    return ap.parse_args()

def main() -> None:
    args = parse_args()

    if not TARGET_ALIASES:
        print("::error ::No TARGET_ALIASES configured in script.")
        sys.exit(1)

    # Bootstrap session
    sess = AmulSession()
    try:
        with_retries(lambda: sess.init_cookies(), tries=RETRIES_INIT, label="bootstrap:cookies")
        with_retries(lambda: sess.set_preference_substore(PINCODE_IN_USE), tries=RETRIES_INIT, label="bootstrap:pref")
    except Exception as e:
        print(f"::notice ::Session init failed (will retry next run): {e}")
        sys.exit(0)

    # Fetch products
    try:
        payload = fetch_by_aliases(sess, TARGET_ALIASES)
    except Exception as e:
        print(f"::notice ::Fetch failed (will retry next run): {e}")
        sys.exit(0)

    items = payload.get("data") or []
    by_alias = {(p.get("alias") or "").strip().lower(): p for p in items}

    missing = [a for a in TARGET_ALIASES if a.strip().lower() not in by_alias]
    if missing:
        log.warning(f"Missing {len(missing)} alias(es) from response: {missing}")

    # State
    state = load_state()
    state.setdefault("tracked", {})
    state.setdefault("history", [])

    summary_lines = [f"### Amul Watch @ {ts()}  |  PIN={PINCODE_IN_USE or 'n/a'}  |  RUN={RUN_CONTEXT}"]
    alert_blocks: List[str] = []

    for a in TARGET_ALIASES:
        key = a.strip().lower()
        cur = by_alias.get(key)
        prev = state["tracked"].get(key, {})

        if cur is None:
            msg = f"⚠️ Alias not present in API response: {a}"
            summary_lines.append(f"- {msg}")
            log.warning(msg)
            continue

        # Availability-only alert logic
        do_alert = should_alert_availability(prev, cur) or FORCE_ALERT

        summary_lines.append(summarize_item(cur))

        if do_alert:
            title = cur.get("name") or cur.get("alias")
            if FORCE_ALERT and not should_alert_availability(prev, cur):
                change_text = "FORCE_ALERT"
            else:
                change_text = "✅ Now AVAILABLE"

            print(f"::warning ::{title} — {change_text}")

            price = cur.get("our_price") or cur.get("price")
            inv = cur.get("inventory_quantity")
            avail = cur.get("available")
            purl = product_url(cur.get("alias"))

            block = (
                f"🛎 <b>{title}</b>\n"
                f"{change_text} : {RUN_CONTEXT} Run | PIN {PINCODE_IN_USE or 'n/a'}\n"
                f"Price: {price} | Inventory: {inv} | Available: {avail}\n"
                f"{purl}"
            )
            alert_blocks.append(block)

            state["history"].append({
                "ts": ts(),
                "alias": cur.get("alias"),
                "name": title,
                "changes": [change_text],
                "snapshot": {
                    "available": avail,
                    "inventory_quantity": inv,
                    "price": price,
                },
                "pin": PINCODE_IN_USE,
            })
        else:
            print(f"::notice ::No availability change for {cur.get('alias')} (available={cur.get('available')})")

        # Update snapshot (only fields we need)
        state["tracked"][key] = {
            "available": cur.get("available"),
            "inventory_quantity": cur.get("inventory_quantity"),
            "our_price": cur.get("our_price"),
            "price": cur.get("price"),
        }

    save_state(state)

    # GitHub job summary (optional)
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write("\n".join(summary_lines) + "\n")
        except Exception as e:
            log.warning(f"Could not write job summary: {e}")

    # Send notifications
    if alert_blocks:
        subject = "Amul Watch Alerts"
        text_plain = "\n\n".join(b.replace("<b>", "").replace("</b>", "") for b in alert_blocks)
        text_html_joined = "\n\n".join(alert_blocks)

        em_err = send_email(subject, text_plain)
        tg_err = send_telegram(text_html_joined)

        if em_err: log.warning(em_err)
        if tg_err: log.warning(tg_err)

    sys.exit(0)

if __name__ == "__main__":
    main()