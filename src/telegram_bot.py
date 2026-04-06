"""
NEXUS IDE — Telegram Bot Integration
======================================
Handles purchase flow, license activation, and support via Telegram bot.

Setup:
  1. Create a bot via @BotFather on Telegram
  2. Set the bot token in Settings → Telegram Bot Token
  3. Set the webhook URL to: https://your-domain/api/telegram/webhook

Purchase flow:
  1. User clicks "Upgrade" in NEXUS IDE → gets a purchase code
  2. User sends the code to the Telegram bot
  3. Bot validates and sends payment link (Telegram Stars / external)
  4. After payment, bot generates and sends a license key
  5. User enters the license key in NEXUS IDE
"""

import json
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
• Docker integration
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
    }
    _save_bot_config(default)
    return default


def _save_bot_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    BOT_CONFIG.write_text(json.dumps(config, indent=2), encoding="utf-8")


def get_bot_token() -> str:
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
        _save_bot_config(config)
        return True, f"Bot connected: @{bot_name}"
    except requests.RequestException as e:
        return False, f"Connection error: {e}"


def set_webhook(domain: str) -> tuple[bool, str]:
    token = get_bot_token()
    if not token:
        return False, "Bot token not set. Configure it in Settings first."
    webhook_url = f"{domain}/api/telegram/webhook"
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/setWebhook",
            json={"url": webhook_url},
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            config = _load_bot_config()
            config["webhook_url"] = webhook_url
            _save_bot_config(config)
            return True, f"Webhook set: {webhook_url}"
        return False, data.get("description", "Failed to set webhook")
    except requests.RequestException as e:
        return False, f"Connection error: {e}"


def _send_message(chat_id: int, text: str, parse_mode: str = "Markdown") -> bool:
    token = get_bot_token()
    if not token:
        return False
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
            },
            timeout=10,
        )
        return True
    except requests.RequestException:
        return False


def _generate_license_key(tier: str) -> str:
    prefix = "NX-PRO" if tier == "pro" else "NX-ENT"
    return f"{prefix}-{secrets.token_hex(8).upper()}"


def handle_webhook(payload: dict) -> dict:
    message = payload.get("message", {})
    if not message:
        return {"ok": True}

    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip()
    username = message.get("from", {}).get("username", "user")

    if not chat_id or not text:
        return {"ok": True}

    if text == "/start":
        _send_message(chat_id, WELCOME_MSG)
    elif text == "/plans":
        _send_message(chat_id, PLANS_MSG)
    elif text == "/status":
        config = _load_bot_config()
        codes = config.get("activated_codes", {})
        user_codes = {k: v for k, v in codes.items()
                      if v.get("username") == username}
        if user_codes:
            active = [k for k, v in user_codes.items() if v.get("status") == "active"]
            if active:
                _send_message(chat_id, f"✅ Active license: `{active[0]}`\nPlan: {user_codes[active[0]].get('tier', 'pro').title()}")
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
                f"Settings → Plans → Enter License Key\n\n"
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
            "Community: Join our Discord for peer support",
        )
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
