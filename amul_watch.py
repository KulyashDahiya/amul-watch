#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import time
import random
import logging
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from urllib.parse import urlencode, quote_plus

import requests
from requests import Session
from requests.cookies import RequestsCookieJar
import smtplib
from email.mime.text import MIMEText

# ------------------------------------------------------------
# Logging
# ------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("amul-watch")

# Silence the LibreSSL warning (harmless for us)
try:
    from urllib3.exceptions import NotOpenSSLWarning  # type: ignore
    import warnings

    warnings.simplefilter("ignore", NotOpenSSLWarning)
except Exception:
    pass

# ------------------------------------------------------------
# Config
# ------------------------------------------------------------
# Aliases to watch (comma-separated). If empty, default to Rose Lassi pack of 30
RAW_ALIASES = os.getenv("AMUL_TARGET_ALIASES", "").strip()
TARGET_ALIASES: List[str] = (
    [a.strip() for a in RAW_ALIASES.split(",") if a.strip()]
    or ["amul-high-protein-rose-lassi-200-ml-or-pack-of-30"]
)

FORCE_ALERT = os.getenv("FORCE_ALERT", "0").strip() in ("1", "true", "True")
PINCODE = os.getenv("PINCODE", "251001").strip()

# State file at repo root
STATE_FILE = Path(__file__).resolve().parent / "state.json"

# Optional notifiers (will be used if present)
EMAIL_FROM = os.getenv("EMAIL_FROM", "").strip()
EMAIL_TO = os.getenv("EMAIL_TO", "").strip()
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587").strip() or "587")
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASS = os.getenv("SMTP_PASS", "").strip()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Amul constants
AMUL_HOST = "https://shop.amul.com"
STORE_ID = "62fa94df8c13af2e242eba16"  # shop.amul.com store id

FIELDS = [
    "name",
    "brand",
    "categories",
    "collections",
    "alias",
    "sku",
    "price",
    "compare_price",
    "original_price",
    "images",
    "metafields",
    "discounts",
    "catalog_only",
    "is_catalog",
    "seller",
    "available",
    "inventory_quantity",
    "net_quantity",
    "num_reviews",
    "avg_rating",
    "inventory_low_stock_quantity",
    "inventory_allow_out_of_stock",
    "default_variant",
    "variants",
    "lp_seller_ids",
    "list_price",
    "our_price",
    "entity_type",
    "inventory_management",
    "linked_product_id",
    "seller_id",
    "inventory_management_level",
]

DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": f"{AMUL_HOST}/",
    "Origin": AMUL_HOST,
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0 Safari/537.36",
    "frontend": "1",
    "Priority": "u=1, i",
    "Connection": "close",
}


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def build_products_url_by_aliases(aliases: List[str]) -> str:
    params = []
    for f in FIELDS:
        params.append((f"fields[{f}]", "1"))
    # important knobs, no explicit substore param — we rely on cookie preference
    params.extend(
        [
            ("facets", "true"),
            ("facetgroup", "default_category_facet"),
            ("total", "1"),
            ("start", "0"),
            ("limit", str(max(32, len(aliases) + 8))),
        ]
    )
    params += [
        ("filters[0][field]", "alias"),
        ("filters[0][operator]", "in"),
        ("filters[0][original]", "1"),
    ]
    for i, a in enumerate(aliases):
        params.append((f"filters[0][value][{i}]", a))
    params.append(("_", str(int(time.time()))))
    return f"{AMUL_HOST}/api/1/entity/ms.products?{urlencode(params, doseq=True, quote_via=quote_plus)}"


def ensure_state_dir() -> Path:
    d = STATE_FILE.parent
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log.warning(f"Could not create state dir {d}: {e}")
    return d


def load_state() -> Dict[str, Any]:
    if STATE_FILE.exists():
        try:
            with STATE_FILE.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.warning(f"Could not read state {STATE_FILE}: {e}")
    return {}


def save_state(state: Dict[str, Any]) -> None:
    ensure_state_dir()
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        tmp.replace(STATE_FILE)
    except Exception as e:
        log.warning(f"Could not write state {STATE_FILE}: {e}")


def summarize_item(p: Dict[str, Any]) -> str:
    alias = p.get("alias")
    name = p.get("name")
    price = p.get("our_price") or p.get("price")
    inv = p.get("inventory_quantity")
    avail = p.get("available")
    return f"- {name} | alias: {alias} | price: {price} | inventory: {inv} | available: {avail}"


def detect_changes(prev: Dict[str, Any], cur: Dict[str, Any]) -> List[str]:
    changes = []

    def gv(d, *keys):
        for k in keys:
            if k in d and d[k] is not None:
                return d[k]
        return None

    p_avail = prev.get("available")
    c_avail = cur.get("available")
    p_inv = prev.get("inventory_quantity")
    c_inv = cur.get("inventory_quantity")
    p_price = gv(prev, "our_price", "price")
    c_price = gv(cur, "our_price", "price")

    if p_avail != c_avail:
        changes.append("✅ Now AVAILABLE" if c_avail else "⛔️ Became UNAVAILABLE")

    if p_inv != c_inv:
        if p_inv is None and c_inv is not None:
            changes.append(f"ℹ️ Inventory set to {c_inv}")
        elif isinstance(p_inv, int) and isinstance(c_inv, int):
            delta = c_inv - p_inv
            if delta > 0:
                changes.append(f"📈 Inventory increased by {delta} → {c_inv}")
            elif delta < 0:
                changes.append(f"📉 Inventory decreased by {-delta} → {c_inv}")

    if p_price != c_price and c_price is not None:
        if p_price is None:
            changes.append(f"💰 Price set to {c_price}")
        else:
            if c_price < p_price:
                changes.append(f"💸 Price drop {p_price} → {c_price}")
            elif c_price > p_price:
                changes.append(f"💵 Price increase {p_price} → {c_price}")

    return changes


def product_url(alias: str) -> str:
    return f"{AMUL_HOST}/en/product/{alias}"


# ------------------------------------------------------------
# Session / cookies / tid
# ------------------------------------------------------------
def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def calc_tid(session_tid: str) -> str:
    """
    Build the 'tid' header value the site expects:
    "<timestamp>:<rand>:<sha256(storeID:timestamp:rand:session_tid)>"
    """
    timestamp = str(int(time.time() * 1000))
    rand = str(int(1000 * random.random()))
    payload = f"{STORE_ID}:{timestamp}:{rand}:{session_tid}".encode("utf-8")
    digest = _sha256_hex(payload)
    return f"{timestamp}:{rand}:{digest}"


def init_session_for_pincode(pincode: str) -> Dict[str, Any]:
    """
    1. GET browse page to receive cookies
    2. GET /user/info.js to obtain session.tid
    3. GET /entity/pincode to find record for provided pincode
    4. PUT /entity/ms.settings/_/setPreferences to set store=substore
    Returns dict with session, cookies, session_tid.
    """
    sess: Session = requests.Session()
    sess.headers.update(DEFAULT_HEADERS)

    # step 1: warm cookies
    r1 = sess.get(f"{AMUL_HOST}/en/browse/protein", timeout=25)
    r1.raise_for_status()

    # step 2: get session tid
    r2 = sess.get(f"{AMUL_HOST}/user/info.js?_v={int(time.time()*1000)}", timeout=25)
    r2.raise_for_status()
    raw = r2.text.strip()
    if not raw.startswith("session = "):
        raise RuntimeError("Unexpected info.js format")
    info = json.loads(raw.replace("session = ", "", 1))
    session_tid = info.get("tid")
    if not session_tid:
        raise RuntimeError("No session tid in info.js")
    log.info("Got session tid.")

    # step 3: pincode search
    r3 = sess.get(
        f"{AMUL_HOST}/entity/pincode",
        params={
            "limit": "50",
            "filters[0][field]": "pincode",
            "filters[0][value]": pincode,
            "filters[0][operator]": "regex",
            "cf_cache": "1h",
        },
        headers={**DEFAULT_HEADERS, "tid": calc_tid(session_tid)},
        timeout=25,
    )
    r3.raise_for_status()
    records = r3.json().get("records") or []
    if not records:
        raise RuntimeError(f"No pincode records for {pincode}")
    record = records[0]  # first match

    # step 4: set preference -> store=substore
    r4 = sess.put(
        f"{AMUL_HOST}/entity/ms.settings/_/setPreferences",
        json={"data": {"store": record.get("substore")}},
        headers={**DEFAULT_HEADERS, "tid": calc_tid(session_tid)},
        timeout=25,
    )
    r4.raise_for_status()
    log.info(f"Preference set for pincode {pincode} (substore {record.get('substore')}).")

    return {"session": sess, "cookies": sess.cookies, "session_tid": session_tid}


def fetch_products_with_session(
    sess: Session, session_tid: str, aliases: List[str], max_retries: int = 4
) -> Dict[str, Any]:
    url = build_products_url_by_aliases(aliases)
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            r = sess.get(url, headers={**DEFAULT_HEADERS, "tid": calc_tid(session_tid)}, timeout=25)
            if r.status_code == 200:
                return r.json()
            last_err = f"{r.status_code} {r.reason}"
            log.warning(f"[try {attempt}] API {last_err} on products")
            time.sleep(0.75 * attempt + random.random())
        except Exception as e:
            last_err = str(e)
            log.warning(f"[try {attempt}] Exception on products: {last_err}")
            time.sleep(0.75 * attempt + random.random())
    raise RuntimeError(f"All fetch attempts failed. Last error: {last_err or 'unknown'}")


# ------------------------------------------------------------
# Notifiers (optional)
# ------------------------------------------------------------
def send_email(subject: str, body: str) -> Optional[str]:
    if not (EMAIL_FROM and EMAIL_TO and SMTP_HOST and SMTP_USER and SMTP_PASS):
        return "email: missing SMTP envs; skipped"
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO
        msg["Subject"] = subject
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
        return None
    except Exception as e:
        return f"email error: {e}"


def send_telegram(text: str) -> Optional[str]:
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return "telegram: missing bot envs; skipped"
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        r = requests.post(url, json=payload, timeout=20)
        if r.status_code != 200:
            return f"telegram {r.status_code}: {r.text[:200]}"
        return None
    except Exception as e:
        return f"telegram error: {e}"


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
def main() -> None:
    # Build session for the PINCODE (sets the correct substore in preferences)
    try:
        sess_info = init_session_for_pincode(PINCODE)
    except Exception as e:
        print(f"::error ::Session init failed: {e}")
        sys.exit(1)

    sess: Session = sess_info["session"]
    session_tid: str = sess_info["session_tid"]

    # Fetch products for our aliases
    try:
        payload = fetch_products_with_session(sess, session_tid, TARGET_ALIASES)
    except Exception as e:
        print(f"::notice ::Fetch failed: {e}")
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

    summary_lines = [f"### Amul Watch @ {ts()}"]
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

        changes = detect_changes(prev, cur)
        should_alert = bool(changes) or FORCE_ALERT

        summary_lines.append(summarize_item(cur))

        if should_alert:
            change_text = " | ".join(changes) if changes else "FORCE_ALERT"
            title = cur.get("name") or cur.get("alias")
            print(f"::warning ::{title} — {change_text}")

            purl = product_url(cur.get("alias"))
            price = cur.get("our_price") or cur.get("price")
            inv = cur.get("inventory_quantity")
            avail = cur.get("available")
            block = (
                f"🛎 <b>{title}</b>\n"
                f"{change_text}\n"
                f"Price: {price} | Inventory: {inv} | Available: {avail}\n"
                f"{purl}"
            )
            alert_blocks.append(block)

            state["history"].append(
                {
                    "ts": ts(),
                    "alias": cur.get("alias"),
                    "name": title,
                    "changes": changes if changes else ["FORCE_ALERT"],
                    "snapshot": {
                        "available": cur.get("available"),
                        "inventory_quantity": inv,
                        "price": price,
                    },
                }
            )
        else:
            print(
                f"::notice ::No change for {cur.get('alias')} "
                f"(available={cur.get('available')}, inv={cur.get('inventory_quantity')})"
            )

        # Update last snapshot
        state["tracked"][key] = {
            "available": cur.get("available"),
            "inventory_quantity": cur.get("inventory_quantity"),
            "our_price": cur.get("our_price"),
            "price": cur.get("price"),
        }

    # Persist
    save_state(state)

    # GitHub job summary (if running in Actions)
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write("\n".join(summary_lines) + "\n")
        except Exception as e:
            log.warning(f"Could not write job summary: {e}")

    # Notifications
    if alert_blocks:
        subject = "Amul Watch Alerts"
        text_plain = "\n\n".join(b.replace("<b>", "").replace("</b>", "") for b in alert_blocks)
        text_html_joined = "<br><br>".join(b.replace("\n", "<br>") for b in alert_blocks)

        em_err = send_email(subject, text_plain)
        tg_err = send_telegram(text_html_joined)

        if em_err:
            log.warning(em_err)
        if tg_err:
            log.warning(tg_err)

    sys.exit(0)


if __name__ == "__main__":
    main()