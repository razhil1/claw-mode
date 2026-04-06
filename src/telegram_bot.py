"""
NEXUS IDE — Telegram Bot Integration
======================================
Handles purchase flow, license activation, and support via Telegram bot.

Setup:
  1. Set TELEGRAM_BOT_TOKEN environment variable
  2. App auto-registers webhook on startup
  3. Users interact with bot on Telegram to purchase and activate licenses

Purchase flow:
  1. User clicks "Upgrade" in NEXUS IDE → gets a purchase code
  2. User sends the code to the Telegram bot
  3. Bot validates and sends payment link (Telegram Stars / external)
  4. After payment, bot generates and sends a license key
  5. User enters the license key in NEXUS IDE
"""

import json
import os
import secrets
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

CONFIG_DIR  = Path.home() / ".config" / "nexus"
BOT_CONFIG  = CONFIG_DIR / "telegram_bot.json"

ADMIN_USERNAMES = {"hiptyhezo"}

WELCOME_MSG = """
🔷 *NEXUS IDE Bot*

Welcome! I can help you with:

/start — Show this menu
/plans — View available plans
/activate CODE — Activate a purchase code
/status — Check your license status
/support — Get help

To upgrade, use the /plans command or visit the Plans page in NEXUS IDE.
"""

PLANS_MSG = """
📋 *NEXUS IDE Plans*

🟢 *Community* — Free
• 50 AI messages/day
• 3 agent modes
• Full editor & terminal

🟣 *Pro* — $19/mo
• Unlimited messages
• All 6 agent modes
• Multi-agent swarm
• Priority model access
• Priority support

🟡 *Enterprise* — $49/mo
• Everything in Pro
• Unlimited team seats
• API access & SSO
• Dedicated support
• On-premise deployment

To upgrade, get a purchase code from NEXUS IDE (Settings → Plans → Upgrade) and send it here with:
`/activate YOUR-CODE`
"""


def _load_bot_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if BOT_CONFIG.exists():
        try:
            return json.loads(BOT_CONFIG.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    default = {
        "bot_token": "",
        "webhook_url": "",
        "activated_codes": {},
        "pending_payments": {},
        "users": {},
    }
    _save_bot_config(default)
    return default


def _save_bot_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    BOT_CONFIG.write_text(json.dumps(config, indent=2), encoding="utf-8")


def get_bot_token() -> str:
    env_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if env_token:
        return env_token
    return _load_bot_config().get("bot_token", "")


def set_bot_token(token: str) -> tuple[bool, str]:
    if not token or len(token) < 20:
        return False, "Invalid bot token format."
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=10,
        )
        data = resp.json()
        if not data.get("ok"):
            return False, f"Telegram API error: {data.get('description', 'Unknown error')}"
        bot_name = data["result"].get("username", "Unknown")
        config = _load_bot_config()
        config["bot_token"] = token
        config["bot_username"] = bot_name
        _save_bot_config(config)
        return True, f"Bot connected: @{bot_name}"
    except requests.RequestException as e:
        return False, f"Connection error: {e}"


def get_bot_info() -> dict:
    token = get_bot_token()
    if not token:
        return {"configured": False}
    try:
        resp = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        data = resp.json()
        if data.get("ok"):
            return {
                "configured": True,
                "username": data["result"].get("username", ""),
                "name": data["result"].get("first_name", ""),
                "id": data["result"].get("id"),
            }
    except requests.RequestException:
        pass
    return {"configured": bool(token), "username": _load_bot_config().get("bot_username", "")}


def set_webhook(domain: str) -> tuple[bool, str]:
    token = get_bot_token()
    if not token:
        return False, "Bot token not set."
    webhook_url = f"{domain}/api/telegram/webhook"
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/setWebhook",
            json={"url": webhook_url, "allowed_updates": ["message"]},
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            config = _load_bot_config()
            config["webhook_url"] = webhook_url
            _save_bot_config(config)
            logger.info("Telegram webhook set: %s", webhook_url)
            return True, f"Webhook set: {webhook_url}"
        return False, data.get("description", "Failed to set webhook")
    except requests.RequestException as e:
        return False, f"Connection error: {e}"


def auto_setup_webhook(app_domain: str) -> None:
    token = get_bot_token()
    if not token:
        return
    try:
        ok, msg = set_webhook(app_domain)
        if ok:
            logger.info("Telegram bot webhook auto-configured: %s", msg)
        else:
            logger.warning("Telegram bot webhook setup failed: %s", msg)
    except Exception as e:
        logger.warning("Telegram bot auto-setup error: %s", e)


def _send_message(chat_id: int, text: str, parse_mode: str = "Markdown",
                  reply_markup: dict = None) -> bool:
    token = get_bot_token()
    if not token:
        return False
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=10,
        )
        return True
    except requests.RequestException:
        return False


def _generate_license_key(tier: str) -> str:
    prefix = "NX-PRO" if tier == "pro" else "NX-ENT"
    return f"{prefix}-{secrets.token_hex(8).upper()}"


def _is_admin(username: str) -> bool:
    return username.lower() in {u.lower() for u in ADMIN_USERNAMES}


def _register_user(chat_id: int, username: str, first_name: str = "") -> None:
    config = _load_bot_config()
    users = config.setdefault("users", {})
    uid = str(chat_id)
    if uid not in users:
        users[uid] = {
            "username": username,
            "first_name": first_name,
            "joined_at": datetime.now().isoformat(),
            "chat_id": chat_id,
        }
    else:
        users[uid]["username"] = username
        users[uid]["last_active"] = datetime.now().isoformat()
    _save_bot_config(config)


def _get_user_count() -> int:
    config = _load_bot_config()
    return len(config.get("users", {}))


def _get_active_licenses() -> list[dict]:
    config = _load_bot_config()
    codes = config.get("activated_codes", {})
    return [
        {"key": k, **v}
        for k, v in codes.items()
        if v.get("status") == "active"
    ]


def handle_webhook(payload: dict) -> dict:
    message = payload.get("message", {})
    if not message:
        return {"ok": True}

    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip()
    username = message.get("from", {}).get("username", "")
    first_name = message.get("from", {}).get("first_name", "")

    if not chat_id or not text:
        return {"ok": True}

    _register_user(chat_id, username, first_name)

    if text == "/start":
        greeting = f"Hi {first_name}! " if first_name else ""
        _send_message(chat_id, f"{greeting}{WELCOME_MSG}")

    elif text == "/plans":
        _send_message(chat_id, PLANS_MSG, reply_markup={
            "inline_keyboard": [
                [{"text": "🟣 Upgrade to Pro — $19/mo", "callback_data": "upgrade_pro"}],
                [{"text": "🟡 Upgrade to Enterprise — $49/mo", "callback_data": "upgrade_enterprise"}],
            ]
        })

    elif text == "/status":
        config = _load_bot_config()
        codes = config.get("activated_codes", {})
        user_codes = {k: v for k, v in codes.items()
                      if v.get("username", "").lower() == username.lower()}
        if user_codes:
            active = [k for k, v in user_codes.items() if v.get("status") == "active"]
            if active:
                key_info = user_codes[active[0]]
                tier = key_info.get("tier", "pro").title()
                activated = key_info.get("activated_at", "N/A")
                _send_message(
                    chat_id,
                    f"✅ *Active License*\n\n"
                    f"Plan: *{tier}*\n"
                    f"License: `{active[0]}`\n"
                    f"Activated: {activated}\n\n"
                    f"Your NEXUS IDE has full access. 🚀",
                )
            else:
                _send_message(chat_id, "No active licenses found. Use /plans to upgrade.")
        else:
            _send_message(chat_id, "No licenses found for your account. Use /plans to upgrade.")

    elif text.startswith("/activate"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            _send_message(chat_id, "Usage: `/activate YOUR-PURCHASE-CODE`")
            return {"ok": True}
        code = parts[1].strip().upper()
        from .plans import _load_plan
        plan = _load_plan()
        purchase_codes = plan.get("purchase_codes", {})
        if code in purchase_codes and purchase_codes[code].get("status") == "pending":
            tier = purchase_codes[code].get("tier", "pro")
            license_key = _generate_license_key(tier)
            purchase_codes[code]["status"] = "activated"
            purchase_codes[code]["activated_by"] = username
            purchase_codes[code]["activated_at"] = datetime.now().isoformat()
            purchase_codes[code]["license_key"] = license_key
            from .plans import _save_plan
            plan["purchase_codes"] = purchase_codes
            _save_plan(plan)
            config = _load_bot_config()
            config.setdefault("activated_codes", {})[license_key] = {
                "tier": tier,
                "username": username,
                "chat_id": chat_id,
                "activated_at": datetime.now().isoformat(),
                "status": "active",
            }
            _save_bot_config(config)
            _send_message(
                chat_id,
                f"🎉 *License Activated!*\n\n"
                f"Plan: *{tier.title()}*\n"
                f"License Key: `{license_key}`\n\n"
                f"Copy this key and paste it in NEXUS IDE:\n"
                f"*Settings → Plans → Enter License Key*\n\n"
                f"Thank you for upgrading! 🚀",
            )
        else:
            _send_message(
                chat_id,
                "❌ Invalid or already used purchase code.\n"
                "Get a new code from NEXUS IDE → Settings → Plans → Upgrade.",
            )

    elif text == "/support":
        _send_message(
            chat_id,
            "💬 *NEXUS IDE Support*\n\n"
            "For help, describe your issue and we'll respond as soon as possible.\n\n"
            "Common issues:\n"
            "• License activation: Use `/activate CODE`\n"
            "• Plan status: Use `/status`\n"
            "• Technical issues: Describe the problem and we'll help\n\n"
            "Developer: @hiptyhezo",
        )

    elif text.startswith("/admin") and _is_admin(username):
        _handle_admin(chat_id, text, username)

    else:
        _send_message(
            chat_id,
            "I didn't understand that command. Try:\n"
            "/start — Menu\n"
            "/plans — View plans\n"
            "/activate CODE — Activate license\n"
            "/status — Check status\n"
            "/support — Get help",
        )

    return {"ok": True}


def _handle_admin(chat_id: int, text: str, username: str) -> None:
    parts = text.split(maxsplit=1)
    subcmd = parts[1].strip() if len(parts) > 1 else ""

    if subcmd == "stats":
        config = _load_bot_config()
        user_count = len(config.get("users", {}))
        active_licenses = len([
            v for v in config.get("activated_codes", {}).values()
            if v.get("status") == "active"
        ])
        pro_count = len([
            v for v in config.get("activated_codes", {}).values()
            if v.get("status") == "active" and v.get("tier") == "pro"
        ])
        ent_count = len([
            v for v in config.get("activated_codes", {}).values()
            if v.get("status") == "active" and v.get("tier") == "enterprise"
        ])
        _send_message(
            chat_id,
            f"📊 *Admin Dashboard*\n\n"
            f"Total Users: {user_count}\n"
            f"Active Licenses: {active_licenses}\n"
            f"  • Pro: {pro_count}\n"
            f"  • Enterprise: {ent_count}\n"
            f"Webhook: {config.get('webhook_url', 'Not set')}",
        )

    elif subcmd == "users":
        config = _load_bot_config()
        users = config.get("users", {})
        if not users:
            _send_message(chat_id, "No users registered yet.")
            return
        lines = ["👥 *Registered Users*\n"]
        for uid, u in list(users.items())[:50]:
            name = u.get("first_name", "")
            uname = u.get("username", "")
            joined = u.get("joined_at", "")[:10]
            lines.append(f"• {name} (@{uname}) — {joined}")
        _send_message(chat_id, "\n".join(lines))

    elif subcmd == "licenses":
        config = _load_bot_config()
        codes = config.get("activated_codes", {})
        active = {k: v for k, v in codes.items() if v.get("status") == "active"}
        if not active:
            _send_message(chat_id, "No active licenses.")
            return
        lines = ["🔑 *Active Licenses*\n"]
        for key, info in active.items():
            lines.append(
                f"• `{key}`\n"
                f"  Tier: {info.get('tier', '?').title()}\n"
                f"  User: @{info.get('username', '?')}\n"
                f"  Date: {info.get('activated_at', '?')[:10]}"
            )
        _send_message(chat_id, "\n".join(lines))

    elif subcmd.startswith("grant "):
        grant_parts = subcmd.split(maxsplit=2)
        if len(grant_parts) < 3:
            _send_message(chat_id, "Usage: `/admin grant @username pro` or `enterprise`")
            return
        target_user = grant_parts[1].lstrip("@")
        tier = grant_parts[2].lower()
        if tier not in ("pro", "enterprise"):
            _send_message(chat_id, "Tier must be `pro` or `enterprise`.")
            return
        license_key = _generate_license_key(tier)
        config = _load_bot_config()
        config.setdefault("activated_codes", {})[license_key] = {
            "tier": tier,
            "username": target_user,
            "chat_id": None,
            "activated_at": datetime.now().isoformat(),
            "granted_by": username,
            "status": "active",
        }
        _save_bot_config(config)
        _send_message(
            chat_id,
            f"✅ *License Granted*\n\n"
            f"User: @{target_user}\n"
            f"Tier: {tier.title()}\n"
            f"Key: `{license_key}`\n\n"
            f"Send this key to the user to activate in NEXUS IDE.",
        )

    elif subcmd.startswith("revoke "):
        key_to_revoke = subcmd.split(maxsplit=1)[1].strip()
        config = _load_bot_config()
        codes = config.get("activated_codes", {})
        if key_to_revoke in codes:
            codes[key_to_revoke]["status"] = "revoked"
            codes[key_to_revoke]["revoked_at"] = datetime.now().isoformat()
            codes[key_to_revoke]["revoked_by"] = username
            _save_bot_config(config)
            _send_message(chat_id, f"🚫 License `{key_to_revoke}` has been revoked.")
        else:
            _send_message(chat_id, f"License key not found: `{key_to_revoke}`")

    elif subcmd.startswith("broadcast "):
        msg = subcmd.split(maxsplit=1)[1].strip()
        if not msg:
            _send_message(chat_id, "Usage: `/admin broadcast Your message here`")
            return
        config = _load_bot_config()
        users = config.get("users", {})
        sent = 0
        for uid, u in users.items():
            cid = u.get("chat_id")
            if cid:
                if _send_message(cid, f"📢 *NEXUS IDE Announcement*\n\n{msg}"):
                    sent += 1
        _send_message(chat_id, f"✅ Broadcast sent to {sent}/{len(users)} users.")

    else:
        _send_message(
            chat_id,
            "🔧 *Admin Commands*\n\n"
            "/admin stats — Bot statistics\n"
            "/admin users — List registered users\n"
            "/admin licenses — List active licenses\n"
            "/admin grant @user pro — Grant a license\n"
            "/admin revoke LICENSE\\_KEY — Revoke a license\n"
            "/admin broadcast MESSAGE — Send to all users",
        )
