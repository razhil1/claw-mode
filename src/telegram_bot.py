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
/help — Full command reference
/plans — View available plans
/trial — Start 7-day free Pro trial
/activate CODE — Activate a purchase code
/status — Check your license status
/referral — Get your referral code
/redeem CODE — Redeem a referral code
/usage — Today's usage stats
/changelog — Latest updates
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
    webhook_secret = hashlib.sha256(token.encode()).hexdigest()[:32]
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/setWebhook",
            json={
                "url": webhook_url,
                "allowed_updates": ["message", "callback_query", "pre_checkout_query"],
                "secret_token": webhook_secret,
            },
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


STAR_PRICES = {
    "pro": {"amount": 950, "label": "NEXUS Pro — 1 Month"},
    "enterprise": {"amount": 2450, "label": "NEXUS Enterprise — 1 Month"},
}

CHANGELOG_TEXT = (
    "📝 *NEXUS IDE Changelog*\n\n"
    "*v2.4 — Latest*\n"
    "• 7-day free Pro trial system\n"
    "• Referral codes (+10 bonus messages)\n"
    "• Telegram Stars payment support\n"
    "• Usage tracking & daily stats\n"
    "• Feature-gated agent modes\n"
    "• Message limit enforcement\n\n"
    "*v2.3*\n"
    "• Telegram bot integration\n"
    "• 3-tier subscription system\n"
    "• License activation flow\n"
    "• Admin dashboard commands\n\n"
    "*v2.2*\n"
    "• 13 built-in tools for agents\n"
    "• Multi-agent swarm mode\n"
    "• Git integration\n"
    "• Environment variable manager"
)


def _send_invoice(chat_id: int, tier: str) -> bool:
    token = get_bot_token()
    if not token or tier not in STAR_PRICES:
        return False
    info = STAR_PRICES[tier]
    payload_str = json.dumps({"tier": tier, "chat_id": chat_id})
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendInvoice",
            json={
                "chat_id": chat_id,
                "title": info["label"],
                "description": f"Unlock all {tier.title()} features in NEXUS IDE for 30 days.",
                "payload": payload_str,
                "currency": "XTR",
                "prices": [{"label": info["label"], "amount": info["amount"]}],
            },
            timeout=10,
        )
        return True
    except requests.RequestException:
        return False


def _handle_callback_query(callback_query: dict) -> dict:
    cq_id = callback_query.get("id", "")
    data = callback_query.get("data", "")
    chat_id = callback_query.get("message", {}).get("chat", {}).get("id")
    username = callback_query.get("from", {}).get("username", "")
    first_name = callback_query.get("from", {}).get("first_name", "")
    token = get_bot_token()

    if token and cq_id:
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                json={"callback_query_id": cq_id},
                timeout=5,
            )
        except requests.RequestException:
            pass

    if not chat_id:
        return {"ok": True}

    _register_user(chat_id, username, first_name)

    if data == "upgrade_pro":
        if _send_invoice(chat_id, "pro"):
            pass
        else:
            _send_message(
                chat_id,
                "To upgrade to *Pro*, get a purchase code from NEXUS IDE:\n"
                "Settings → Plans → Upgrade → send `/activate CODE` here.",
            )
    elif data == "upgrade_enterprise":
        if _send_invoice(chat_id, "enterprise"):
            pass
        else:
            _send_message(
                chat_id,
                "To upgrade to *Enterprise*, get a purchase code from NEXUS IDE:\n"
                "Settings → Plans → Upgrade → send `/activate CODE` here.",
            )
    elif data == "start_trial":
        from .plans import start_free_trial
        ok, msg = start_free_trial()
        _send_message(chat_id, f"{'🎉' if ok else '⚠️'} {msg}")
    elif data == "get_referral":
        from .plans import generate_referral_code
        code = generate_referral_code()
        _send_message(chat_id, f"🔗 Your referral code: `{code}`")
    elif data == "view_changelog":
        _send_message(chat_id, CHANGELOG_TEXT)

    return {"ok": True}


def _handle_pre_checkout(pre_checkout_query: dict) -> dict:
    token = get_bot_token()
    if not token:
        return {"ok": True}
    pcq_id = pre_checkout_query.get("id", "")
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/answerPreCheckoutQuery",
            json={"pre_checkout_query_id": pcq_id, "ok": True},
            timeout=10,
        )
    except requests.RequestException:
        pass
    return {"ok": True}


def _handle_successful_payment(message: dict) -> dict:
    chat_id = message.get("chat", {}).get("id")
    username = message.get("from", {}).get("username", "")
    payment = message.get("successful_payment", {})
    payload_str = payment.get("invoice_payload", "{}")
    try:
        payload_data = json.loads(payload_str)
    except (json.JSONDecodeError, TypeError):
        payload_data = {}

    tier = payload_data.get("tier", "pro")
    license_key = _generate_license_key(tier)

    config = _load_bot_config()
    config.setdefault("activated_codes", {})[license_key] = {
        "tier": tier,
        "username": username,
        "chat_id": chat_id,
        "activated_at": datetime.now().isoformat(),
        "status": "active",
        "payment_method": "telegram_stars",
        "total_amount": payment.get("total_amount", 0),
        "telegram_payment_charge_id": payment.get("telegram_payment_charge_id", ""),
    }
    config.setdefault("payments", []).append({
        "username": username,
        "chat_id": chat_id,
        "tier": tier,
        "amount": payment.get("total_amount", 0),
        "currency": payment.get("currency", "XTR"),
        "charge_id": payment.get("telegram_payment_charge_id", ""),
        "provider_charge_id": payment.get("provider_payment_charge_id", ""),
        "timestamp": datetime.now().isoformat(),
    })
    _save_bot_config(config)

    _send_message(
        chat_id,
        f"🎉 *Payment Successful!*\n\n"
        f"Plan: *{tier.title()}*\n"
        f"License Key: `{license_key}`\n\n"
        f"Copy this key and paste it in NEXUS IDE:\n"
        f"*Settings → Plans → Enter License Key*\n\n"
        f"Thank you for your purchase! 🚀",
    )
    return {"ok": True}


def verify_webhook_secret(request_header: str) -> bool:
    token = get_bot_token()
    if not token:
        return True
    expected = hashlib.sha256(token.encode()).hexdigest()[:32]
    return request_header == expected


def handle_webhook(payload: dict) -> dict:
    if "callback_query" in payload:
        return _handle_callback_query(payload["callback_query"])

    if "pre_checkout_query" in payload:
        return _handle_pre_checkout(payload["pre_checkout_query"])

    message = payload.get("message", {})
    if not message:
        return {"ok": True}

    if "successful_payment" in message:
        return _handle_successful_payment(message)

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
                [{"text": "🆓 Start Free Trial (7 days)", "callback_data": "start_trial"}],
                [
                    {"text": "🔗 Get Referral Code", "callback_data": "get_referral"},
                    {"text": "📝 Changelog", "callback_data": "view_changelog"},
                ],
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

    elif text == "/trial":
        from .plans import start_free_trial
        ok, msg = start_free_trial()
        if ok:
            _send_message(
                chat_id,
                "🎉 *Free Trial Activated!*\n\n"
                f"{msg}\n\n"
                "Your trial includes:\n"
                "• Unlimited AI messages\n"
                "• All 6 agent modes\n"
                "• Multi-agent swarm mode\n"
                "• Priority model access\n\n"
                "Trial expires in 7 days. Upgrade anytime with /plans.",
            )
        else:
            _send_message(chat_id, f"⚠️ {msg}")

    elif text == "/referral":
        from .plans import generate_referral_code
        code = generate_referral_code()
        _send_message(
            chat_id,
            f"🔗 *Your Referral Code*\n\n"
            f"`{code}`\n\n"
            f"Share this code with friends! When they redeem it:\n"
            f"• They get *+10 bonus messages/day*\n"
            f"• You get credit toward your account\n\n"
            f"Friends can redeem with: `/redeem {code}`",
        )

    elif text.startswith("/redeem"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            _send_message(chat_id, "Usage: `/redeem NXREF-XXXXXXXX`")
            return {"ok": True}
        ref_code = parts[1].strip().upper()
        from .plans import redeem_referral_code
        ok, msg = redeem_referral_code(ref_code)
        emoji = "✅" if ok else "❌"
        _send_message(chat_id, f"{emoji} {msg}")

    elif text == "/usage":
        from .plans import get_usage_stats
        stats = get_usage_stats()
        if stats["unlimited"]:
            usage_text = "♾️ Unlimited"
        else:
            usage_text = f"{stats['used']} / {stats['effective_limit']}"
            if stats["bonus"] > 0:
                usage_text += f" (+{stats['bonus']} bonus)"
        trial_text = ""
        if stats.get("trial_active"):
            trial_text = f"\n🆓 Trial expires: {stats['trial_expires'][:10]}"
        _send_message(
            chat_id,
            f"📊 *Usage Stats*\n\n"
            f"Plan: *{stats['tier'].title()}*\n"
            f"Messages today: {usage_text}\n"
            f"Referrals: {stats.get('referrals_count', 0)}"
            f"{trial_text}",
        )

    elif text == "/changelog":
        _send_message(
            chat_id,
            "📝 *NEXUS IDE Changelog*\n\n"
            "*v2.4 — Latest*\n"
            "• 7-day free Pro trial system\n"
            "• Referral codes (+10 bonus messages)\n"
            "• Telegram Stars payment support\n"
            "• Usage tracking & daily stats\n"
            "• Feature-gated agent modes\n"
            "• Message limit enforcement\n\n"
            "*v2.3*\n"
            "• Telegram bot integration\n"
            "• 3-tier subscription system\n"
            "• License activation flow\n"
            "• Admin dashboard commands\n\n"
            "*v2.2*\n"
            "• 13 built-in tools for agents\n"
            "• Multi-agent swarm mode\n"
            "• Git integration\n"
            "• Environment variable manager",
        )

    elif text == "/help":
        _send_message(
            chat_id,
            "📖 *NEXUS IDE Bot — Command Reference*\n\n"
            "*General*\n"
            "/start — Welcome message\n"
            "/help — This reference\n"
            "/plans — View plans & pricing\n"
            "/changelog — Latest updates\n\n"
            "*Subscription*\n"
            "/trial — Start 7-day free Pro trial\n"
            "/activate CODE — Activate purchase code\n"
            "/status — Check license status\n"
            "/usage — Today's usage stats\n\n"
            "*Referrals*\n"
            "/referral — Get your referral code\n"
            "/redeem CODE — Redeem a referral\n\n"
            "*Support*\n"
            "/support — Contact support\n\n"
            "Developer: @hiptyhezo",
        )

    elif text == "/support":
        _send_message(
            chat_id,
            "💬 *NEXUS IDE Support*\n\n"
            "For help, describe your issue and we'll respond as soon as possible.\n\n"
            "Common issues:\n"
            "• License activation: Use `/activate CODE`\n"
            "• Plan status: Use `/status`\n"
            "• Start a free trial: Use `/trial`\n"
            "• Referral bonus: Use `/referral`\n"
            "• Technical issues: Describe the problem and we'll help\n\n"
            "Developer: @hiptyhezo",
        )

    elif text.startswith("/admin") and _is_admin(username):
        _handle_admin(chat_id, text, username)

    else:
        _send_message(
            chat_id,
            "I didn't understand that command. Try /help for all commands.\n\n"
            "Quick reference:\n"
            "/plans — View plans\n"
            "/trial — Free 7-day Pro trial\n"
            "/activate CODE — Activate license\n"
            "/status — Check status\n"
            "/referral — Get referral code",
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
        payments = config.get("payments", [])
        total_revenue = sum(p.get("amount", 0) for p in payments)
        recent_payments = len([p for p in payments if p.get("timestamp", "")[:10] == datetime.now().strftime("%Y-%m-%d")])
        _send_message(
            chat_id,
            f"📊 *Admin Dashboard*\n\n"
            f"👥 Total Users: {user_count}\n"
            f"🔑 Active Licenses: {active_licenses}\n"
            f"  • Pro: {pro_count}\n"
            f"  • Enterprise: {ent_count}\n"
            f"💰 Total Payments: {len(payments)}\n"
            f"  • Revenue (Stars): {total_revenue}\n"
            f"  • Today: {recent_payments} payments\n"
            f"🔗 Webhook: {config.get('webhook_url', 'Not set')}",
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

    elif subcmd == "payments":
        config = _load_bot_config()
        payments = config.get("payments", [])
        if not payments:
            _send_message(chat_id, "No payments recorded yet.")
            return
        lines = ["💳 *Recent Payments*\n"]
        for p in payments[-20:]:
            lines.append(
                f"• @{p.get('username', '?')} — {p.get('tier', '?').title()}\n"
                f"  {p.get('amount', 0)} {p.get('currency', 'XTR')} — {p.get('timestamp', '?')[:16]}"
            )
        _send_message(chat_id, "\n".join(lines))

    elif subcmd == "trials":
        from .plans import _load_plan
        plan = _load_plan()
        trial_info = "No trial active."
        if plan.get("trial_active"):
            trial_info = f"Trial active, expires: {plan.get('trial_expires', 'N/A')[:10]}"
        elif plan.get("trial_used"):
            trial_info = f"Trial already used (started: {plan.get('trial_started', 'N/A')[:10]})"
        _send_message(chat_id, f"🆓 *Trial Status*\n\n{trial_info}")

    else:
        _send_message(
            chat_id,
            "🔧 *Admin Commands*\n\n"
            "/admin stats — Bot statistics & revenue\n"
            "/admin users — List registered users\n"
            "/admin licenses — List active licenses\n"
            "/admin payments — Recent payment history\n"
            "/admin trials — Trial status\n"
            "/admin grant @user pro — Grant a license\n"
            "/admin revoke LICENSE\\_KEY — Revoke a license\n"
            "/admin broadcast MESSAGE — Send to all users",
        )
