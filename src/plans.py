"""
NEXUS IDE — Plans & Licensing System
=====================================
Three tiers: Community (free), Pro, Enterprise.
License keys stored in ~/.config/nexus/plan.json.
Feature gating for backend routes and agent capabilities.
"""

import json
import hashlib
import secrets
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

CONFIG_DIR = Path.home() / ".config" / "nexus"
PLAN_FILE  = CONFIG_DIR / "plan.json"

TIERS = {
    "community": {
        "name": "Community",
        "price": "Free",
        "price_monthly": 0,
        "badge_color": "#4ade80",
        "features": {
            "messages_per_day": 50,
            "max_sessions": 1,
            "agent_modes": ["auto", "builder", "debugger"],
            "multi_agent": False,
            "priority_models": False,
            "custom_providers": False,
            "docker_integration": False,
            "db_explorer": True,
            "git_integration": True,
            "file_management": True,
            "code_editor": True,
            "terminal": True,
            "memory_learning": True,
            "knowledge_base": True,
            "export_zip": True,
            "community_access": True,
            "support": "community",
            "max_file_size_mb": 5,
            "workspace_size_mb": 100,
        },
        "highlights": [
            "50 AI messages per day",
            "3 agent modes (Auto, Builder, Debugger)",
            "Full code editor & terminal",
            "Git integration",
            "Community support",
        ],
    },
    "pro": {
        "name": "Pro",
        "price": "$19/mo",
        "price_monthly": 19,
        "badge_color": "#a78bfa",
        "features": {
            "messages_per_day": -1,
            "max_sessions": 5,
            "agent_modes": ["auto", "builder", "debugger", "refactorer", "researcher", "reviewer"],
            "multi_agent": True,
            "priority_models": True,
            "custom_providers": True,
            "docker_integration": True,
            "db_explorer": True,
            "git_integration": True,
            "file_management": True,
            "code_editor": True,
            "terminal": True,
            "memory_learning": True,
            "knowledge_base": True,
            "export_zip": True,
            "community_access": True,
            "support": "priority",
            "max_file_size_mb": 50,
            "workspace_size_mb": 1000,
        },
        "highlights": [
            "Unlimited AI messages",
            "All 6 agent modes",
            "Multi-agent swarm mode",
            "Priority model access",
            "Custom AI providers",
            "Docker integration",
            "Priority support",
        ],
    },
    "enterprise": {
        "name": "Enterprise",
        "price": "$49/mo",
        "price_monthly": 49,
        "badge_color": "#f59e0b",
        "features": {
            "messages_per_day": -1,
            "max_sessions": -1,
            "agent_modes": ["auto", "builder", "debugger", "refactorer", "researcher", "reviewer"],
            "multi_agent": True,
            "priority_models": True,
            "custom_providers": True,
            "docker_integration": True,
            "db_explorer": True,
            "git_integration": True,
            "file_management": True,
            "code_editor": True,
            "terminal": True,
            "memory_learning": True,
            "knowledge_base": True,
            "export_zip": True,
            "community_access": True,
            "support": "dedicated",
            "max_file_size_mb": 500,
            "workspace_size_mb": 10000,
            "team_seats": -1,
            "api_access": True,
            "custom_branding": True,
            "sso": True,
            "audit_log": True,
            "on_premise": True,
        },
        "highlights": [
            "Everything in Pro",
            "Unlimited team seats",
            "API access",
            "SSO / SAML authentication",
            "Audit logging",
            "Custom branding",
            "On-premise deployment",
            "Dedicated support",
        ],
    },
}


def _load_plan() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if PLAN_FILE.exists():
        try:
            return json.loads(PLAN_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    default = {
        "tier": "community",
        "license_key": None,
        "activated_at": None,
        "expires_at": None,
        "messages_today": 0,
        "last_message_date": None,
        "purchase_codes": {},
    }
    _save_plan(default)
    return default


def _save_plan(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PLAN_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_current_plan() -> dict:
    plan_data = _load_plan()
    tier = plan_data.get("tier", "community")
    if tier not in TIERS:
        tier = "community"
    if plan_data.get("expires_at"):
        try:
            exp = datetime.fromisoformat(plan_data["expires_at"])
            if datetime.now() > exp:
                plan_data["tier"] = "community"
                plan_data["license_key"] = None
                plan_data["expires_at"] = None
                _save_plan(plan_data)
                tier = "community"
        except (ValueError, TypeError):
            pass
    today = datetime.now().strftime("%Y-%m-%d")
    if plan_data.get("last_message_date") != today:
        plan_data["messages_today"] = 0
        plan_data["last_message_date"] = today
        _save_plan(plan_data)
    tier_info = TIERS[tier].copy()
    tier_info["current_tier"] = tier
    tier_info["messages_today"] = plan_data.get("messages_today", 0)
    tier_info["license_key"] = plan_data.get("license_key")
    tier_info["activated_at"] = plan_data.get("activated_at")
    tier_info["expires_at"] = plan_data.get("expires_at")
    return tier_info


def get_all_plans() -> dict:
    current = get_current_plan()
    result = {}
    for tid, info in TIERS.items():
        plan = info.copy()
        plan["is_current"] = tid == current["current_tier"]
        result[tid] = plan
    return result


def check_message_limit() -> tuple[bool, str]:
    plan = _load_plan()
    tier = plan.get("tier", "community")
    limit = TIERS.get(tier, TIERS["community"])["features"]["messages_per_day"]
    if limit == -1:
        return True, "unlimited"
    today = datetime.now().strftime("%Y-%m-%d")
    if plan.get("last_message_date") != today:
        plan["messages_today"] = 0
        plan["last_message_date"] = today
    if plan["messages_today"] >= limit:
        return False, f"Daily limit reached ({limit} messages). Upgrade to Pro for unlimited."
    return True, f"{plan['messages_today']}/{limit}"


def increment_message_count() -> int:
    plan = _load_plan()
    today = datetime.now().strftime("%Y-%m-%d")
    if plan.get("last_message_date") != today:
        plan["messages_today"] = 0
        plan["last_message_date"] = today
    plan["messages_today"] = plan.get("messages_today", 0) + 1
    _save_plan(plan)
    return plan["messages_today"]


def check_feature(feature_name: str) -> bool:
    plan = get_current_plan()
    return plan.get("features", {}).get(feature_name, False)


def generate_purchase_code(target_tier: str) -> str:
    if target_tier not in ("pro", "enterprise"):
        return ""
    code = f"NX-{target_tier.upper()}-{secrets.token_hex(4).upper()}"
    plan = _load_plan()
    plan.setdefault("purchase_codes", {})[code] = {
        "tier": target_tier,
        "created_at": datetime.now().isoformat(),
        "status": "pending",
    }
    _save_plan(plan)
    return code


def activate_license(license_key: str) -> tuple[bool, str]:
    if not license_key or len(license_key) < 10:
        return False, "Invalid license key format."
    key_lower = license_key.strip().upper()
    if key_lower.startswith("NX-PRO-"):
        tier = "pro"
    elif key_lower.startswith("NX-ENT-"):
        tier = "enterprise"
    elif key_lower.startswith("NEXUS-PRO-"):
        tier = "pro"
    elif key_lower.startswith("NEXUS-ENT-"):
        tier = "enterprise"
    else:
        h = hashlib.sha256(license_key.encode()).hexdigest()[:8]
        if h[0] in "0123":
            tier = "pro"
        elif h[0] in "456789":
            tier = "enterprise"
        else:
            tier = "pro"

    plan = _load_plan()
    plan["tier"] = tier
    plan["license_key"] = license_key.strip()
    plan["activated_at"] = datetime.now().isoformat()
    plan["expires_at"] = (datetime.now() + timedelta(days=365)).isoformat()
    _save_plan(plan)
    return True, f"License activated! You now have {TIERS[tier]['name']} access."


def deactivate_license() -> tuple[bool, str]:
    plan = _load_plan()
    plan["tier"] = "community"
    plan["license_key"] = None
    plan["activated_at"] = None
    plan["expires_at"] = None
    _save_plan(plan)
    return True, "License deactivated. Reverted to Community plan."
