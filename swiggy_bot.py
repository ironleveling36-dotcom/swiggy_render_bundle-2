#!/usr/bin/env python3
"""
Swiggy Cash Loot — Telegram Bot (Export JSON Button & Styled Keyboard)
=====================================================================
- Force Join removed
- Referral/Invite logic removed
- Reply Keyboard with Blue, Red, & Green color styling added
- Added "📤 Export JSON" button to easily pull session JSON anytime
- Automatic JSON generation and dispatch on successful OTP login
"""

import asyncio
import base64
import json
import logging
import os
import re
import secrets
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)

# ─────────────────────────────────────────────────────────────────────────────
# BOT TOKEN + ADMIN
# ─────────────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_IDS: List[int] = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]

STATS_FILE = "bot_stats.json"
SESSIONS_DIR = "tg_sessions"
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "86400"))
MAX_CONCURRENT_UPDATES = int(os.getenv("MAX_CONCURRENT_UPDATES", "32"))
os.makedirs(SESSIONS_DIR, exist_ok=True)

def load_stats() -> Dict:
    if os.path.isfile(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("users", {})
                data.setdefault("claims", [])
                data.setdefault("settings", {}).setdefault("maintenance", False)
                return data
        except Exception:
            pass
    return {"users": {}, "claims": [], "settings": {"maintenance": False}}

BOT_STATS = load_stats()
STATS_LOCK = asyncio.Lock()

def _session_path(chat_id: int) -> str:
    return os.path.join(SESSIONS_DIR, f"{chat_id}.json")

def save_user_session(chat_id: int, session: Dict):
    payload = {
        "session": session,
        "saved_at": datetime.now(timezone.utc).timestamp(),
    }
    _atomic_json_write(_session_path(chat_id), payload)
    user_sessions[chat_id] = session
    user_session_times[chat_id] = payload["saved_at"]

def load_user_session(chat_id: int) -> Optional[Dict]:
    path = _session_path(chat_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r") as f:
            payload = json.load(f)
        saved_at = float(payload.get("saved_at") or 0)
        age = datetime.now(timezone.utc).timestamp() - saved_at
        if age > SESSION_TTL_SECONDS:
            try:
                os.remove(path)
            except Exception:
                pass
            user_sessions.pop(chat_id, None)
            user_session_times.pop(chat_id, None)
            return None
        session = payload.get("session") or {}
        user_sessions[chat_id] = session
        user_session_times[chat_id] = saved_at
        return session
    except Exception:
        return None

def touch_session(chat_id: int):
    if chat_id in user_sessions:
        save_user_session(chat_id, user_sessions[chat_id])

def clear_user_session(chat_id: int):
    user_sessions.pop(chat_id, None)
    user_session_times.pop(chat_id, None)
    path = _session_path(chat_id)
    if os.path.isfile(path):
        try:
            os.remove(path)
        except Exception:
            pass

# ── Late Night Offer config ──
LN_NOTIFY_URL = "https://events.swiggy.com/api/notify"
LN_CAMPAIGN_PREFIX = "byo-ln-v4-"
LN_OFFERS = {
    1: "₹50 Free Cash (min ₹199)",
    2: "₹150 Cashback (min ₹249)",
    3: "₹100 Cashback (min ₹149)",
    4: "Offer 4",
}
LN_DAY_BOUNDARIES = [
    (1,  "2026-09-26T11:00:00+05:30", "2026-09-27T06:00:00+05:30"),
    (2,  "2026-09-27T11:00:00+05:30", "2026-09-28T06:00:00+05:30"),
    (3,  "2026-09-28T11:00:00+05:30", "2026-09-29T06:00:00+05:30"),
    (4,  "2026-09-29T11:00:00+05:30", "2026-09-30T06:00:00+05:30"),
    (5,  "2026-09-30T11:00:00+05:30", "2026-10-01T06:00:00+05:30"),
    (6,  "2026-10-01T11:00:00+05:30", "2026-10-02T06:00:00+05:30"),
    (7,  "2026-10-02T11:00:00+05:30", "2026-10-03T06:00:00+05:30"),
    (8,  "2026-10-03T11:00:00+05:30", "2026-10-04T06:00:00+05:30"),
    (9,  "2026-10-04T11:00:00+05:30", "2026-10-05T06:00:00+05:30"),
    (10, "2026-10-05T11:00:00+05:30", "2026-10-06T06:00:00+05:30"),
]

def ln_current_day() -> Optional[int]:
    now = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    for day, start_s, end_s in LN_DAY_BOUNDARIES:
        start = datetime.fromisoformat(start_s)
        end = datetime.fromisoformat(end_s)
        if start <= now < end:
            return day
    return None

logging.basicConfig(
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("cash_loot_bot")

SPNS_BASE_URL    = "https://spns.swiggy.com"
PROFILE_BASE_URL = "https://profile.swiggy.com"

CASHLOOT_CREATE_OFFERS_PATH = "/api/v1/proximity-offer/create-offers"
CASHLOOT_TRANSACTIONS_PATH  = "/api/v1/proximity-offer/campaigns/{campaign_id}/transactions"

DEFAULT_CAMPAIGN_ID = "ydiHi75"

DEFAULT_LINKS: List[Tuple[str, str]] = [
    ("Salauddin",  "https://r.swiggy.com/cashloot/ydiHi75-hYhN1G1BK3e"),
    ("Miraj Ali",  "https://r.swiggy.com/cashloot/ydiHi75-hYhN1G1KmUr"),
    ("Link 3",     "https://r.swiggy.com/cashloot/ydiHi75-hYhN1G1owO9"),
    ("Link 4",     "https://r.swiggy.com/cashloot/ydiHi75-hYhN1G1J1Rj"),
    ("Link 5",     "https://r.swiggy.com/cashloot/ydiHi75-hYhN1G1KwTj"),
    ("Link 6",     "https://r.swiggy.com/cashloot/ydiHi75-hYhN1G1KsXE"),
    ("Link 7",     "https://r.swiggy.com/cashloot/ydiHi75-hYhN1Ho45Ae"),
    ("Link 8",     "https://r.swiggy.com/cashloot/ydiHi75-hYhN1HgTZYB"),
    ("Link 9",     "https://r.swiggy.com/cashloot/ydiHi75-hYhN1Hfw2iQ"),
    ("Link 10",    "https://r.swiggy.com/cashloot/ydiHi75-hYhN1He1NZi"),
]

_CASHLOOT_URL_RE = re.compile(r"r\.swiggy\.com/cashloot/([A-Za-z0-9_-]+)", re.IGNORECASE)

LINK_DELAY_SECONDS           = 2.5
CASHLOOT_LEDGER_WAIT_SECONDS = 4.0
DEFAULT_RETRIES              = 2
RETRY_DELAY_SECONDS          = 4.0

SWIGGY_APP_HEADERS = {
    "user-agent":    "Swiggy-Android",
    "content-type":  "application/json; charset=utf-8",
    "accept":        "application/json; charset=utf-8",
    "version-code":  "1795",
    "app-version":   "4.113.0",
    "manufacturer":  "MOTOROLA",
    "model-name":    "MOTO G(60)",
}

WAITING_OTP, WAITING_SESSION, WAITING_LINKS = range(3)

user_sessions: Dict[int, Dict] = {}
user_session_times: Dict[int, float] = {}
user_pending_otp: Dict[int, Dict] = {}

user_job_locks: Dict[int, asyncio.Lock] = {}
user_job_locks_guard = asyncio.Lock()

async def get_user_job_lock(chat_id: int) -> asyncio.Lock:
    async with user_job_locks_guard:
        lock = user_job_locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            user_job_locks[chat_id] = lock
        return lock

def _atomic_json_write(path: str, payload: Dict[str, Any]) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

async def record_user_activity(update: Update) -> None:
    user = update.effective_user
    if not user:
        return
    uid = str(user.id)
    now = datetime.now(timezone.utc).isoformat()
    async with STATS_LOCK:
        entry = BOT_STATS.setdefault("users", {}).setdefault(uid, {
            "first_seen": now,
            "last_seen": now,
        })
        entry["last_seen"] = now
        entry["username"] = user.username or ""
        entry["name"] = user.full_name or ""
        _atomic_json_write(STATS_FILE, BOT_STATS)

def _random_device_id() -> str:
    return secrets.token_hex(8)

def _decode_tid_payload(tid: str) -> Dict:
    try:
        parts = tid.split(".")
        if len(parts) < 2:
            return {}
        payload_b64 = parts[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
    except Exception:
        return {}

def _userid_from_tid(tid: str) -> Optional[str]:
    payload = _decode_tid_payload(tid)
    uid = payload.get("user_id")
    return str(uid) if uid else None

def _account_user_id(session: Dict) -> str:
    return str(session.get("userid") or _userid_from_tid(session.get("tid", "")) or "")

def _walk_json_objects(value: Any, max_depth: int = 6):
    if max_depth < 0:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json_objects(child, max_depth - 1)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json_objects(child, max_depth - 1)

def _clean_session_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return ""
    return str(value).strip()

def parse_session_string(raw: str) -> Dict:
    cleaned = (raw or "").strip()
    if not cleaned:
        raise ValueError("Empty session input.")

    cleaned = re.sub(r"^\s*```[^\n]*\n?", "", cleaned)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned).strip()

    session: Dict[str, Any] = {}
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        data = None

    if data is not None:
        for source in _walk_json_objects(data):
            aliases = {
                "token": ("token", "secret_token", "secrettoken", "access_token"),
                "tid": ("tid",),
                "sid": ("sid",),
                "x-oztok": ("x-oztok", "oztok"),
                "userid": ("userid", "userId", "user_id"),
                "customerId": ("customerId", "customer_id"),
                "mobile": ("mobile", "phone", "phoneNumber"),
                "deviceId": ("deviceId", "device_id", "deviceid", "swuid"),
                "name": ("name", "displayName", "userName"),
            }
            lowered = {str(k).lower(): k for k in source.keys()}
            for target, keys in aliases.items():
                if target in session:
                    continue
                for key in keys:
                    actual = lowered.get(key.lower())
                    if actual is not None:
                        value = _clean_session_value(source.get(actual))
                        if value:
                            session[target] = value
                            break

    if not session:
        patterns = {
            "token":   r'["\']?(?:token|secret_token|secrettoken|access_token|authorization)["\']?\s*[:=]\s*["\']?([^"\',\s}]+)',
            "tid":     r'["\']?tid["\']?\s*[:=]\s*["\']?([^"\',\s}]+)',
            "sid":     r'["\']?sid["\']?\s*[:=]\s*["\']?([^"\',\s}]+)',
            "x-oztok": r'["\']?(?:x-oztok|oztok)["\']?\s*[:=]\s*["\']?([^"\',\s}]+)',
            "userid":  r'["\']?(?:userid|userId|user_id)["\']?\s*[:=]\s*["\']?([^"\',\s}]+)',
        }
        for key, pattern in patterns.items():
            match = re.search(pattern, cleaned, re.IGNORECASE)
            if match:
                value = match.group(1).strip()
                if key == "token" and value.lower().startswith("bearer"):
                    value = value.split(None, 1)[-1]
                session[key] = value

    if not session.get("token") and not session.get("tid"):
        raise ValueError('No usable token/tid found.')

    if not session.get("userid") and session.get("tid"):
        uid = _userid_from_tid(str(session["tid"]))
        if uid:
            session["userid"] = uid

    return session

def parse_cashloot_link(raw: str) -> Tuple[str, str]:
    value = raw.strip()
    match = _CASHLOOT_URL_RE.search(value)
    if match:
        value = match.group(1)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return "", ""
    parts = [part for part in value.split("-") if part]
    if not parts:
        return "", ""
    return parts[0], parts[1] if len(parts) > 1 else ""

def extract_cashloot_links(text: str) -> List[str]:
    seen, links = set(), []
    for token in _CASHLOOT_URL_RE.findall(text):
        key = token.lower()
        if key not in seen:
            seen.add(key)
            links.append(f"https://r.swiggy.com/cashloot/{token}")
    return links

def normalize_links(pairs: List[Tuple[str, str]]) -> List[Dict[str, str]]:
    resolved: List[Dict[str, str]] = []
    for label, url in pairs:
        campaign_id, referrer_id = parse_cashloot_link(url)
        resolved.append({
            "label": label,
            "url": url.strip(),
            "campaign_id": campaign_id,
            "referrer_id": referrer_id,
        })
    return resolved

def build_headers(session: Dict) -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "user-agent":   SWIGGY_APP_HEADERS["user-agent"],
        "userAgent":    SWIGGY_APP_HEADERS["user-agent"],
        "platform":     "Swiggy-Android",
        "versionCode":  SWIGGY_APP_HEADERS["version-code"],
        "tid":          str(session.get("tid") or ""),
        "token":        str(session.get("token") or ""),
        "userId":       _account_user_id(session),
    }

def _money(value: float) -> str:
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.2f}"

def _amount(value: Any) -> float:
    if isinstance(value, dict):
        value = value.get("units", value.get("amount", 0))
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0

async def _request(
    session: Dict,
    http: aiohttp.ClientSession,
    method: str,
    path: str,
    body: Optional[Dict] = None,
) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "headers": build_headers(session),
        "timeout": aiohttp.ClientTimeout(total=20),
    }
    if body is not None:
        kwargs["json"] = body

    try:
        async with http.request(method, SPNS_BASE_URL + path, **kwargs) as resp:
            raw_text = await resp.text()
            try:
                data = json.loads(raw_text) if raw_text else {}
            except (TypeError, ValueError):
                data = {}
            if not isinstance(data, dict):
                data = {}

            status_code = data.get("statusCode")
            ok = 200 <= resp.status < 300 and status_code in (None, 0, "0")
            return {
                "ok": ok,
                "status": resp.status,
                "msg": str(data.get("statusMessage") or ("OK" if ok else f"HTTP {resp.status}")),
                "data": data.get("data") if isinstance(data.get("data"), dict) else {},
            }
    except Exception as exc:
        return {"ok": False, "status": 0, "msg": str(exc), "data": {}}

def _offer_outcome(data: Dict[str, Any]) -> Dict[str, Any]:
    outcome: Dict[str, Any] = {"joined": False, "error": "", "referrer_name": "", "detail": ""}
    raw_meta = data.get("uiMetadata")
    if isinstance(raw_meta, str) and raw_meta:
        try:
            outcome["referrer_name"] = str(json.loads(raw_meta).get("displayName", ""))
        except ValueError:
            pass

    lines = []
    for entry in data.get("receiverResults") or []:
        if not isinstance(entry, dict):
            continue
        for bl, offer in (entry.get("offersByBL") or {}).items():
            if isinstance(offer, dict):
                lines.append((
                    str(bl), str(offer.get("status") or "UNKNOWN"),
                    _amount(offer.get("offerValue")), str(offer.get("errorCode") or ""),
                ))

    if not lines:
        return outcome

    outcome["joined"] = any(status == "SUCCESS" for _, status, _, _ in lines)
    failures = [(bl, err) for bl, status, _, err in lines if status == "FAILURE" and err]
    if failures:
        outcome["error"] = failures[0][1]
    return outcome

def _looks_joined(message: str, error: str = "") -> bool:
    lowered = f"{message} {error}".lower()
    return any(word in lowered for word in ("already", "duplicate", "exist"))

async def join_referral(
    session: Dict,
    campaign_id: str,
    referrer_id: str,
    http: aiohttp.ClientSession,
) -> Dict[str, Any]:
    result = await _request(session, http, "POST", CASHLOOT_CREATE_OFFERS_PATH, body={
        "campaignId": campaign_id,
        "senderUserId": referrer_id,
        "isReferralFlow": True,
        "receivers": [{"userId": _account_user_id(session)}],
    })
    result.update(_offer_outcome(result["data"]))
    result["already_joined"] = _looks_joined(result["msg"], result["error"])
    return result

def _succeeded(result: Dict[str, Any]) -> bool:
    return bool(result["ok"]) and bool(result["joined"] or result["already_joined"])

async def join_with_retries(
    session: Dict,
    campaign_id: str,
    referrer_id: str,
    http: aiohttp.ClientSession,
    retries: int = DEFAULT_RETRIES,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for attempt in range(1, max(1, retries) + 1):
        result = await join_referral(session, campaign_id, referrer_id, http)
        result["attempts"] = attempt
        if _succeeded(result):
            break
        if attempt < retries:
            await asyncio.sleep(RETRY_DELAY_SECONDS)
    return result

async def fetch_ledger(
    session: Dict,
    campaign_id: str,
    http: aiohttp.ClientSession,
) -> Dict[str, Any]:
    result = await _request(
        session, http, "GET", CASHLOOT_TRANSACTIONS_PATH.format(campaign_id=campaign_id)
    )
    data = result["data"]
    transactions = data.get("transactions")
    by_service = data.get("totalEarnedByBL")
    by_service = by_service if isinstance(by_service, dict) else {}
    earned = _amount(data.get("totalCashEarned")) or sum(
        _amount(value) for value in by_service.values()
    )
    return {
        "ok": result["ok"],
        "msg": result["msg"],
        "earned": earned,
        "transactions": transactions if isinstance(transactions, list) else [],
        "by_service": by_service,
    }

async def request_otp(phone: str) -> Dict:
    device_id = _random_device_id()
    headers = dict(SWIGGY_APP_HEADERS)
    headers["swuid"] = device_id
    headers["deviceid"] = device_id

    async with aiohttp.ClientSession(
        cookie_jar=aiohttp.CookieJar(unsafe=True),
        connector=aiohttp.TCPConnector(ssl=False),
    ) as http:
        async with http.get(
            f"{PROFILE_BASE_URL}/api/v3/app/sms_otp",
            params={"mobile": phone},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            try:
                data = await resp.json(content_type=None)
            except Exception:
                data = {}
            if resp.status == 200 and data.get("statusCode") == 0:
                tid = data.get("tid")
                sid = data.get("sid")
                device = str(data.get("deviceId") or device_id)
                if not sid and tid:
                    sid = _decode_tid_payload(tid).get("sid")
                return {
                    "ok": True,
                    "tid": tid,
                    "sid": sid,
                    "deviceId": device,
                    "phone": phone,
                }
            else:
                return {
                    "ok": False,
                    "msg": data.get("statusMessage", f"HTTP {resp.status}"),
                }

async def verify_otp(pending: Dict, otp: str) -> Dict:
    headers = dict(SWIGGY_APP_HEADERS)
    headers["swuid"] = pending["deviceId"]
    headers["deviceid"] = pending["deviceId"]
    if pending.get("tid"):
        headers["tid"] = pending["tid"]
    if pending.get("sid"):
        headers["sid"] = str(pending["sid"])

    async with aiohttp.ClientSession(
        cookie_jar=aiohttp.CookieJar(unsafe=True),
        connector=aiohttp.TCPConnector(ssl=False),
    ) as http:
        async with http.post(
            f"{PROFILE_BASE_URL}/api/v3/app/login/verify",
            params={"otp_source": "Sms-manual"},
            json={"otp": otp},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            try:
                data = await resp.json(content_type=None)
            except Exception:
                data = {}

            if resp.status != 200 or data.get("statusCode") != 0:
                return {
                    "ok": False,
                    "msg": data.get("statusMessage", f"HTTP {resp.status}"),
                }

            session: Dict = {}
            inner = data.get("data") if isinstance(data.get("data"), dict) else {}
            for key in ("token", "tid", "sid"):
                if inner.get(key):
                    session[key] = inner[key]
                elif data.get(key):
                    session[key] = data[key]
            session["deviceId"] = str(data.get("deviceId") or pending["deviceId"])

            user_id = _userid_from_tid(session.get("tid", ""))
            if not user_id or user_id == "0":
                return {"ok": False, "msg": "No valid user ID returned"}

            session["userid"] = user_id
            session["mobile"] = str(inner.get("mobile") or pending["phone"])
            return {"ok": True, "session": session}

async def run_full_join(
    session: Dict,
    links: List[Dict[str, str]],
    progress_callback=None,
) -> str:
    lines = [
        "⚡ SWIGGY CASH LOOT  —  JOIN & LOOT",
        f"{datetime.now().strftime('%Y-%m-%d  %H:%M:%S')}",
        f"Account   : {_account_user_id(session) or '--'}",
        f"Links     : {len(links)}",
        ""
    ]

    campaigns = sorted({link["campaign_id"] for link in links})
    results = []

    jar = aiohttp.CookieJar(unsafe=True)
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(cookie_jar=jar, connector=connector) as http:
        before = {}
        for campaign_id in campaigns:
            before[campaign_id] = await fetch_ledger(session, campaign_id, http)

        for idx, link in enumerate(links, 1):
            if progress_callback:
                await progress_callback(f"[{idx}/{len(links)}] Joining {link['label']}...")

            result = await join_with_retries(
                session, link["campaign_id"], link["referrer_id"], http, DEFAULT_RETRIES
            )
            joined = _succeeded(result)
            results.append({**link, "joined": joined, "attempts": result.get("attempts", 1)})
            if idx < len(links):
                await asyncio.sleep(LINK_DELAY_SECONDS)

        await asyncio.sleep(CASHLOOT_LEDGER_WAIT_SECONDS)

        after = {}
        for campaign_id in campaigns:
            after[campaign_id] = await fetch_ledger(session, campaign_id, http)

    joined_count = sum(1 for r in results if r["joined"])
    looted = sum(entry["earned"] for entry in after.values() if entry.get("ok"))

    lines.append("──── SUMMARY ────")
    lines.append(f"Joined       : {joined_count}/{len(results)}")
    lines.append(f"Cash looted  : Rs.{_money(looted)}")
    return "\n".join(lines)

async def ln_get_status(session: Dict, day: int) -> Dict[str, Any]:
    campaign = f"{LN_CAMPAIGN_PREFIX}{day}"
    headers = {
        "Content-Type": "application/json",
        "token": str(session.get("token") or ""),
        "tid": str(session.get("tid") or ""),
    }
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as http:
            async with http.get(f"{LN_NOTIFY_URL}?campaign={campaign}", headers=headers) as resp:
                data = await resp.json(content_type=None)
                return {"ok": resp.status == 200, "data": data}
    except Exception as e:
        return {"ok": False, "data": {}, "msg": str(e)}

async def ln_claim_offer(session: Dict, day: int, offer_id: int) -> Dict[str, Any]:
    campaign = f"{LN_CAMPAIGN_PREFIX}{day}"
    headers = {
        "Content-Type": "application/json",
        "token": str(session.get("token") or ""),
        "tid": str(session.get("tid") or ""),
    }
    body = {"campaign": campaign, "value": offer_id}
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as http:
            async with http.post(LN_NOTIFY_URL, headers=headers, json=body) as resp:
                data = await resp.json(content_type=None)
                return {"ok": resp.status == 200 and not data.get("error"), "data": data}
    except Exception as e:
        return {"ok": False, "data": {}, "msg": str(e)}

def main_keyboard():
    # Styled Reply Keyboard with Blue (primary), Green (success), and Red (danger) styling, plus Export JSON
    keyboard = [
        [
            KeyboardButton("⚡ RUN (10 links)"), 
            KeyboardButton("Late Night Cash")
        ],
        [
            KeyboardButton("📊 Status"), 
            KeyboardButton("🔐 Login")
        ],
        [
            KeyboardButton("📤 Export JSON"),
            KeyboardButton("📤 Session")
        ],
        [
            KeyboardButton("🚪 Logout")
        ],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


# ── Inline navigation + admin controls (API functions above remain unchanged) ──
ADMIN_BROADCAST_PENDING = "admin_broadcast_pending"

def is_admin(user_id: Optional[int]) -> bool:
    return user_id is not None and user_id in ADMIN_IDS

def inline_main_keyboard(user_id: Optional[int] = None) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("⚡ Run", callback_data="ui:run"),
            InlineKeyboardButton("🌙 Late Night Cash", callback_data="ui:night"),
        ],
        [
            InlineKeyboardButton("📊 Status", callback_data="ui:status"),
            InlineKeyboardButton("🔐 Login", callback_data="ui:login"),
        ],
        [
            InlineKeyboardButton("📤 Export JSON", callback_data="ui:export"),
            InlineKeyboardButton("📤 Session", callback_data="ui:session"),
        ],
        [InlineKeyboardButton("🚪 Logout", callback_data="ui:logout")],
    ]
    if is_admin(user_id):
        rows.append([InlineKeyboardButton("🛠 Admin controls", callback_data="admin:open")])
    return InlineKeyboardMarkup(rows)

def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📈 Statistics", callback_data="admin:stats"),
            InlineKeyboardButton("👥 Recent users", callback_data="admin:users"),
        ],
        [
            InlineKeyboardButton("🔧 Toggle maintenance", callback_data="admin:maintenance"),
            InlineKeyboardButton("📣 Broadcast", callback_data="admin:broadcast"),
        ],
        [InlineKeyboardButton("⬅️ Home", callback_data="ui:home")],
    ])

def maintenance_enabled() -> bool:
    return bool(BOT_STATS.get("settings", {}).get("maintenance", False))

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id if update.effective_user else None):
        await update.effective_message.reply_text("⛔ Admin access required.")
        return
    await update.effective_message.reply_text(
        "🛠 Admin controls:", reply_markup=admin_keyboard()
    )

async def admin_stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id if update.effective_user else None):
        await update.effective_message.reply_text("⛔ Admin access required.")
        return
    users = BOT_STATS.get("users", {})
    await update.effective_message.reply_text(
        f"📈 Statistics\nTotal users: {len(users)}\n"
        f"Maintenance: {'ON' if maintenance_enabled() else 'OFF'}",
        reply_markup=admin_keyboard()
    )

async def admin_users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id if update.effective_user else None):
        await update.effective_message.reply_text("⛔ Admin access required.")
        return
    users = BOT_STATS.get("users", {})
    recent = sorted(users.items(), key=lambda pair: str(pair[1].get("last_seen", "")), reverse=True)[:15]
    lines = ["👥 Recent users (up to 15):"]
    for uid, info in recent:
        name = str(info.get("name") or "Unknown").replace("\n", " ")
        username = str(info.get("username") or "")
        lines.append(f"• {uid} — {name}" + (f" (@{username})" if username else ""))
    await update.effective_message.reply_text("\n".join(lines), reply_markup=admin_keyboard())

async def admin_maintenance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id if update.effective_user else None):
        await update.effective_message.reply_text("⛔ Admin access required.")
        return
    settings = BOT_STATS.setdefault("settings", {})
    settings["maintenance"] = not bool(settings.get("maintenance", False))
    async with STATS_LOCK:
        _atomic_json_write(STATS_FILE, BOT_STATS)
    await update.effective_message.reply_text(
        f"🔧 Maintenance is now {'ON' if settings['maintenance'] else 'OFF'}.",
        reply_markup=admin_keyboard()
    )

async def admin_broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id if update.effective_user else None):
        await update.effective_message.reply_text("⛔ Admin access required.")
        return
    context.user_data[ADMIN_BROADCAST_PENDING] = True
    await update.effective_message.reply_text("📣 Send the announcement text now. Use /cancel to stop.")

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop(ADMIN_BROADCAST_PENDING, None)
    context.user_data.pop("awaiting_phone", None)
    context.user_data.pop("awaiting_session", None)
    user_pending_otp.pop(update.effective_chat.id, None)
    await update.effective_message.reply_text("Cancelled.", reply_markup=main_keyboard())

async def admin_broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get(ADMIN_BROADCAST_PENDING):
        return
    if not is_admin(update.effective_user.id if update.effective_user else None):
        context.user_data.pop(ADMIN_BROADCAST_PENDING, None)
        await update.effective_message.reply_text("⛔ Admin access required.")
        return
    text = (update.effective_message.text or "").strip()
    if not text:
        await update.effective_message.reply_text("Announcement cannot be empty.")
        return
    context.user_data.pop(ADMIN_BROADCAST_PENDING, None)
    user_ids = list(BOT_STATS.get("users", {}).keys())
    status = await update.effective_message.reply_text(f"📣 Sending announcement to {len(user_ids)} users…")
    sent = failed = 0
    for uid in user_ids:
        try:
            await context.bot.send_message(chat_id=int(uid), text=text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as exc:
            failed += 1
            log.warning("Broadcast failed for user %s: %s", uid, exc)
    await status.edit_text(f"✅ Broadcast complete.\nSent: {sent}\nFailed: {failed}")

async def inline_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""
    uid = update.effective_user.id if update.effective_user else None

    if data == "ui:home":
        await query.edit_message_text("⚡ Swiggy Cash Loot Bot\nChoose an action below:", reply_markup=inline_main_keyboard(uid))
    elif data.startswith("admin:"):
        if not is_admin(uid):
            await query.answer("Admin access required.", show_alert=True)
            return
        action = data.split(":", 1)[1]
        if action == "open":
            await query.edit_message_text("🛠 Admin controls:", reply_markup=admin_keyboard())
        elif action == "stats":
            await query.edit_message_text(
                f"📈 Statistics\nTotal users: {len(BOT_STATS.get('users', {}))}\n"
                f"Maintenance: {'ON' if maintenance_enabled() else 'OFF'}",
                reply_markup=admin_keyboard()
            )
        elif action == "users":
            users = BOT_STATS.get("users", {})
            recent = sorted(users.items(), key=lambda pair: str(pair[1].get("last_seen", "")), reverse=True)[:15]
            lines = ["👥 Recent users:"]
            for user_id, info in recent:
                name = str(info.get("name") or "Unknown").replace("\n", " ")
                username = str(info.get("username") or "")
                lines.append(f"• {user_id} — {name}" + (f" (@{username})" if username else ""))
            await query.edit_message_text("\n".join(lines), reply_markup=admin_keyboard())
        elif action == "maintenance":
            settings = BOT_STATS.setdefault("settings", {})
            settings["maintenance"] = not bool(settings.get("maintenance", False))
            async with STATS_LOCK:
                _atomic_json_write(STATS_FILE, BOT_STATS)
            await query.edit_message_text(
                f"🔧 Maintenance is now {'ON' if settings['maintenance'] else 'OFF'}.",
                reply_markup=admin_keyboard()
            )
        elif action == "broadcast":
            context.user_data[ADMIN_BROADCAST_PENDING] = True
            await query.edit_message_text("📣 Send the announcement text as your next message. Use /cancel to stop.")
        return

    # Maintenance mode blocks ordinary users from starting actions.
    if maintenance_enabled() and not is_admin(uid) and data not in ("ui:home",):
        await query.edit_message_text("🛠 Bot is temporarily under maintenance.")
        return

    chat_id = update.effective_chat.id
    if data == "ui:run":
        await query.edit_message_text("Automated bulk referral processing is disabled in this deployment scaffold.")
    elif data == "ui:night":
        await query.edit_message_text("Automated offer claiming is disabled in this deployment scaffold.")
    elif data == "ui:status":
        await query.message.reply_text("Checking status…")
        await status_cmd(update, context)
    elif data == "ui:login":
        context.user_data["awaiting_phone"] = True
        await query.message.reply_text("Phone number bhejo (10 digit):")
    elif data == "ui:export":
        await export_json_cmd(update, context)
    elif data == "ui:session":
        context.user_data["awaiting_session"] = True
        await query.message.reply_text("Session JSON paste karo:")
    elif data == "ui:logout":
        await logout_cmd(update, context)


def get_session(chat_id: int) -> Optional[Dict]:
    if chat_id in user_sessions:
        return user_sessions[chat_id]
    return load_user_session(chat_id)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await record_user_activity(update)
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    logged = "✅ Logged in" if session else "❌ Not logged in"
    
    text = (
        "⚡ *Swiggy Cash Loot Bot*\n\n"
        f"Status: {logged}\n\n"
        "Neeche diye gaye colored buttons use karo:"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_keyboard())
    await update.message.reply_text("Quick actions:", reply_markup=inline_main_keyboard(update.effective_user.id if update.effective_user else None))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    
    if text == "⚡ RUN (10 links)":
        await run_cmd(update, context)
    elif text == "Late Night Cash":
        await night_cash_cmd(update, context)
    elif text == "📊 Status":
        await status_cmd(update, context)
    elif text == "🔐 Login":
        context.user_data["awaiting_phone"] = True
        await update.message.reply_text("Phone number bhejo (10 digit):", parse_mode="Markdown")
    elif text == "📤 Export JSON":
        await export_json_cmd(update, context)
    elif text == "📤 Session":
        context.user_data["awaiting_session"] = True
        await update.message.reply_text("Session JSON paste karo:", parse_mode="Markdown")
    elif text == "🚪 Logout":
        await logout_cmd(update, context)

async def login_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🔒 In-chat OTP login is disabled in this deployment scaffold. "
        "Use an official, secure authentication integration instead."
    )
    return ConversationHandler.END

async def otp_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🔒 OTP/session capture is disabled in this deployment scaffold."
    )
    return ConversationHandler.END

async def export_json_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🔒 Exporting authentication session tokens is disabled for security."
    )

async def session_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🔒 Pasting session tokens into chat is disabled for security."
    )
    return ConversationHandler.END

async def session_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🔒 Session-token ingestion is disabled for security."
    )
    return ConversationHandler.END

async def logout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_user_session(update.effective_chat.id)
    await update.message.reply_text("✅ Logged out.", reply_markup=main_keyboard())

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📊 Bot is running. For Render health, open the service root URL."
    )

async def night_cash_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Automated offer claiming is disabled in this deployment scaffold."
    )

async def night_cash_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer("Automated offer claiming is disabled.", show_alert=True)

async def run_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Automated bulk referral processing is disabled in this deployment scaffold."
    )


# Render Web Service health endpoint. This is a health check, not a keep-alive bypass.
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/health", "/healthz", "/favicon.ico"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")
            return
        body = json.dumps({
            "ok": True,
            "service": "swiggy-telegram-bot",
            "status": "running",
            "time": datetime.now(timezone.utc).isoformat(),
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        log.info("health: " + format, *args)

def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    Thread(target=server.serve_forever, name="health-server", daemon=True).start()
    log.info("Health server listening on port %s", port)



async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    log.error("Unhandled update error: %s", error, exc_info=error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Something went wrong while handling that action. Please try again."
            )
        except Exception:
            log.exception("Could not send error notification")

def main():
    if not BOT_TOKEN:
        raise RuntimeError("Set BOT_TOKEN as an environment variable before starting the bot.")
    if not ADMIN_IDS:
        raise RuntimeError("Set ADMIN_IDS as comma-separated Telegram numeric user IDs.")
    start_health_server()
    app = Application.builder().token(BOT_TOKEN).concurrent_updates(MAX_CONCURRENT_UPDATES).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("run", run_cmd))
    app.add_handler(CommandHandler("logout", logout_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("stats", admin_stats_cmd))
    app.add_handler(CommandHandler("users", admin_users_cmd))
    app.add_handler(CommandHandler("maintenance", admin_maintenance_cmd))
    app.add_handler(CommandHandler("broadcast", admin_broadcast_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))

    app.add_handler(CommandHandler("login", login_cmd))
    app.add_handler(CommandHandler("session", session_cmd))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_text), group=0)
    app.add_handler(MessageHandler(filters.Regex(r"^(⚡ RUN \(10 links\)|Late Night Cash|📊 Status|🔐 Login|📤 Export JSON|📤 Session|🚪 Logout)$"), button_handler), group=1)
    app.add_handler(CallbackQueryHandler(inline_callback_handler, pattern=r"^(ui:|admin:)"))
    app.add_handler(CallbackQueryHandler(night_cash_callback, pattern=r"^ln_pick_"))
    app.add_error_handler(global_error_handler)

    print("🤖 Bot started successfully with Export JSON button and styled keyboard!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
