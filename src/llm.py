import os
import json
import urllib.request
import urllib.error
from typing import List, Dict

# ===================== RUNTIME KEY STORE =====================
_runtime_keys: dict[str, str] = {}

def set_runtime_key(provider: str, key: str):
    _runtime_keys[provider] = key

def get_key(provider: str, env_var: str) -> str:
    return _runtime_keys.get(provider) or os.environ.get(env_var, "")


# ===================== VERIFIED MODEL CATALOG =====================
# All models verified against live OpenRouter /api/v1/models on 2026-04-01
# Groq models verified against https://console.groq.com/docs/models
#
# Role tags:
#   "fast"      — Low latency, good for iteration
#   "thinking"  — Chain-of-thought / reasoning
#   "coding"    — Specialized for code generation
#   "powerful"  — Best quality for complex tasks
#   "balanced"  — Good all-around quality

GROQ_MODELS: dict[str, dict] = {
    "groq:llama-3.3-70b-versatile": {
        "label": "Llama 3.3 70B Versatile",
        "short": "Best All-Rounder",
        "description": "Meta's best open-source model on ultra-fast Groq hardware. Excellent at coding, reasoning, and instruction-following.",
        "context": 128000, "tier": "free", "provider": "groq",
        "groq_id": "llama-3.3-70b-versatile",
        "role": "balanced",
        "emoji": "⚡",
    },
    "groq:deepseek-r1-distill-llama-70b": {
        "label": "DeepSeek R1 Distill 70B",
        "short": "Fast Thinker",
        "description": "DeepSeek's reasoning model distilled into Llama 70B, served on Groq for very fast chain-of-thought.",
        "context": 128000, "tier": "free", "provider": "groq",
        "groq_id": "deepseek-r1-distill-llama-70b",
        "role": "thinking",
        "emoji": "🧠",
    },
    "groq:llama-3.1-8b-instant": {
        "label": "Llama 3.1 8B Instant",
        "short": "Ultra-Fast",
        "description": "Lightning-fast small model on Groq. Ideal for quick edits, refactoring, and simple tasks.",
        "context": 128000, "tier": "free", "provider": "groq",
        "groq_id": "llama-3.1-8b-instant",
        "role": "fast",
        "emoji": "⚡",
    },
    "groq:gemma2-9b-it": {
        "label": "Gemma 2 9B",
        "short": "Balanced",
        "description": "Google's Gemma 2 on Groq. Efficient and capable for most coding tasks.",
        "context": 8192, "tier": "free", "provider": "groq",
        "groq_id": "gemma2-9b-it",
        "role": "balanced",
        "emoji": "⚡",
    },
    "groq:mixtral-8x7b-32768": {
        "label": "Mixtral 8x7B MoE",
        "short": "MoE Model",
        "description": "Mistral's sparse mixture-of-experts model. Strong instruction following with 32K context.",
        "context": 32768, "tier": "free", "provider": "groq",
        "groq_id": "mixtral-8x7b-32768",
        "role": "balanced",
        "emoji": "⚡",
    },
}

OPENROUTER_FREE_MODELS: dict[str, dict] = {
    "meta-llama/llama-3.3-70b-instruct:free": {
        "label": "Llama 3.3 70B Instruct",
        "short": "Free General",
        "description": "Meta's Llama 3.3 70B via OpenRouter. Reliable, large-context, great for full-stack codegen.",
        "context": 65536, "tier": "free", "provider": "openrouter",
        "role": "balanced",
        "emoji": "✦",
    },
    "qwen/qwen3-coder:free": {
        "label": "Qwen3 Coder 480B",
        "short": "Best Free Coder",
        "description": "Alibaba's Qwen3 Coder — purpose-built for code. 262K context window. Best free coding model available.",
        "context": 262000, "tier": "free", "provider": "openrouter",
        "role": "coding",
        "emoji": "💻",
    },
    "openai/gpt-oss-120b:free": {
        "label": "GPT-OSS 120B",
        "short": "OpenAI Free 120B",
        "description": "OpenAI's open-source 120B model. Powerful, well-rounded, and completely free.",
        "context": 131072, "tier": "free", "provider": "openrouter",
        "role": "powerful",
        "emoji": "✦",
    },
    "openai/gpt-oss-20b:free": {
        "label": "GPT-OSS 20B",
        "short": "OpenAI Free 20B",
        "description": "OpenAI's open-source 20B model. Fast and capable for most coding tasks. Free tier.",
        "context": 131072, "tier": "free", "provider": "openrouter",
        "role": "fast",
        "emoji": "✦",
    },
    "nvidia/nemotron-3-super-120b-a12b:free": {
        "label": "Nemotron 120B Super",
        "short": "NVIDIA Free 120B",
        "description": "NVIDIA's Nemotron 120B parameter model. Very large, strong at complex multi-step reasoning.",
        "context": 262144, "tier": "free", "provider": "openrouter",
        "role": "powerful",
        "emoji": "✦",
    },
    "qwen/qwen3.6-plus-preview:free": {
        "label": "Qwen3.6 Plus Preview",
        "short": "1M Context Free",
        "description": "Alibaba's Qwen3.6+ with an enormous 1 million token context window. Free preview access.",
        "context": 1000000, "tier": "free", "provider": "openrouter",
        "role": "powerful",
        "emoji": "✦",
    },
    "google/gemma-3-27b-it:free": {
        "label": "Gemma 3 27B",
        "short": "Google Free",
        "description": "Google's Gemma 3 27B instruction-tuned. Capable and well-aligned for coding tasks.",
        "context": 131072, "tier": "free", "provider": "openrouter",
        "role": "balanced",
        "emoji": "✦",
    },
    "nousresearch/hermes-3-llama-3.1-405b:free": {
        "label": "Hermes 3 Llama 405B",
        "short": "Largest Free",
        "description": "NousResearch's fine-tuned Llama 3.1 405B — the largest freely available model. Exceptional at complex tasks.",
        "context": 131072, "tier": "free", "provider": "openrouter",
        "role": "powerful",
        "emoji": "✦",
    },
    "liquid/lfm-2.5-1.2b-thinking:free": {
        "label": "LFM 2.5 Thinking",
        "short": "Free Thinking",
        "description": "Liquid AI's small thinking model with visible reasoning chains. Fast and free.",
        "context": 32768, "tier": "free", "provider": "openrouter",
        "role": "thinking",
        "emoji": "🧠",
    },
}

OPENROUTER_PAID_MODELS: dict[str, dict] = {
    "google/gemini-2.0-flash-001": {
        "label": "Gemini 2.0 Flash",
        "short": "Fastest Premium",
        "description": "Google's Gemini 2.0 Flash — 1M context, blazing fast, very affordable. Best speed-to-quality ratio.",
        "context": 1048576, "tier": "paid", "provider": "openrouter",
        "role": "fast",
        "emoji": "★",
        "price_note": "$0.10/1M tokens",
    },
    "meta-llama/llama-4-scout": {
        "label": "Llama 4 Scout",
        "short": "Meta Fast",
        "description": "Meta's Llama 4 Scout — 328K context, fast and affordable. Great for large codebase analysis.",
        "context": 327680, "tier": "paid", "provider": "openrouter",
        "role": "fast",
        "emoji": "★",
        "price_note": "$0.08/1M tokens",
    },
    "deepseek/deepseek-chat-v3-0324": {
        "label": "DeepSeek V3 Chat",
        "short": "Coding Powerhouse",
        "description": "DeepSeek V3 — exceptional coding quality at an ultra-low price. 164K context. Top-tier code generation.",
        "context": 163840, "tier": "paid", "provider": "openrouter",
        "role": "coding",
        "emoji": "💻",
        "price_note": "$0.20/1M tokens",
    },
    "mistralai/devstral-small": {
        "label": "Devstral Small",
        "short": "Code Specialist",
        "description": "Mistral's Devstral — built specifically for software engineering. Best coding model per dollar.",
        "context": 131072, "tier": "paid", "provider": "openrouter",
        "role": "coding",
        "emoji": "💻",
        "price_note": "$0.10/1M tokens",
    },
    "mistralai/codestral-2508": {
        "label": "Codestral 2508",
        "short": "Code Expert",
        "description": "Mistral's code-focused model with 256K context. Excellent for large files and multi-file refactors.",
        "context": 256000, "tier": "paid", "provider": "openrouter",
        "role": "coding",
        "emoji": "💻",
        "price_note": "$0.30/1M tokens",
    },
    "meta-llama/llama-4-maverick": {
        "label": "Llama 4 Maverick",
        "short": "Meta Powerful",
        "description": "Meta's Llama 4 Maverick — 1M context MoE model. Excellent reasoning and code for the price.",
        "context": 1048576, "tier": "paid", "provider": "openrouter",
        "role": "balanced",
        "emoji": "★",
        "price_note": "$0.15/1M tokens",
    },
    "google/gemini-2.5-flash": {
        "label": "Gemini 2.5 Flash",
        "short": "Google Best Value",
        "description": "Google's Gemini 2.5 Flash — 1M context, excellent at code and reasoning. Very affordable.",
        "context": 1048576, "tier": "paid", "provider": "openrouter",
        "role": "balanced",
        "emoji": "★",
        "price_note": "$0.30/1M tokens",
    },
    "deepseek/deepseek-r1-0528": {
        "label": "DeepSeek R1 (May 2025)",
        "short": "Best Reasoning",
        "description": "DeepSeek R1 — state-of-the-art chain-of-thought reasoning. Best free-tier adjacent for complex problem solving.",
        "context": 163840, "tier": "paid", "provider": "openrouter",
        "role": "thinking",
        "emoji": "🧠",
        "price_note": "$0.45/1M tokens",
    },
    "anthropic/claude-3.7-sonnet:thinking": {
        "label": "Claude 3.7 Sonnet (Thinking)",
        "short": "Extended Reasoning",
        "description": "Claude 3.7 Sonnet with extended thinking enabled. Best model for hard algorithmic and architectural problems.",
        "context": 200000, "tier": "paid", "provider": "openrouter",
        "role": "thinking",
        "emoji": "🧠",
        "price_note": "$3.00/1M tokens",
    },
    "anthropic/claude-sonnet-4": {
        "label": "Claude Sonnet 4",
        "short": "Best Overall",
        "description": "Anthropic's Claude Sonnet 4 — the gold standard for coding and complex full-stack development.",
        "context": 200000, "tier": "paid", "provider": "openrouter",
        "role": "powerful",
        "emoji": "★",
        "price_note": "$3.00/1M tokens",
    },
    "google/gemini-2.5-pro": {
        "label": "Gemini 2.5 Pro",
        "short": "Google Frontier",
        "description": "Google's Gemini 2.5 Pro with 1M context. Exceptional at multi-file reasoning and code generation.",
        "context": 1048576, "tier": "paid", "provider": "openrouter",
        "role": "powerful",
        "emoji": "★",
        "price_note": "$1.25/1M tokens",
    },
}

# Smart combo models — multi-model routing
SMART_MODELS: dict[str, dict] = {
    "smart:groq": {
        "label": "Smart Combo (Groq)",
        "short": "Auto-Routing Free",
        "description": "Automatically routes each task to the best Groq model: DeepSeek R1 for planning, Llama 70B for coding, Llama 8B for quick lookups.",
        "context": 128000, "tier": "free", "provider": "smart",
        "role": "balanced",
        "emoji": "🤖",
        "routing": {
            "thinking": "groq:deepseek-r1-distill-llama-70b",
            "coding":   "groq:llama-3.3-70b-versatile",
            "fast":     "groq:llama-3.1-8b-instant",
            "default":  "groq:llama-3.3-70b-versatile",
        },
    },
    "smart:free": {
        "label": "Smart Combo (Free OR)",
        "short": "Auto-Routing OR Free",
        "description": "Automatically routes to best free OpenRouter models: Qwen3 Coder for coding, GPT-OSS 120B for reasoning, Llama 70B for chat.",
        "context": 131072, "tier": "free", "provider": "smart",
        "role": "balanced",
        "emoji": "🤖",
        "routing": {
            "thinking": "openai/gpt-oss-120b:free",
            "coding":   "qwen/qwen3-coder:free",
            "fast":     "openai/gpt-oss-20b:free",
            "default":  "meta-llama/llama-3.3-70b-instruct:free",
        },
    },
}

ALL_MODELS: dict[str, dict] = {
    **SMART_MODELS,
    **GROQ_MODELS,
    **OPENROUTER_FREE_MODELS,
    **OPENROUTER_PAID_MODELS,
}

DEFAULT_MODEL = "groq:llama-3.3-70b-versatile"

# Tools that warrant using the coding-specialist model
CODING_TOOLS = {"FileEditTool", "BashTool"}
# Tools that can use the fast model
FAST_TOOLS = {"ListDirTool", "FileReadTool", "ViewFileLinesTool"}


def _post_json(url: str, headers: dict, body: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class LLMClient:
    """
    Unified LLM client supporting Groq, OpenRouter, and Smart multi-model routing.

    For smart: models, route() must be called before each chat() to tell the client
    what kind of turn this is (thinking / coding / fast / default).
    """

    def __init__(self, model: str = None):
        self.model = model or os.environ.get("CLAW_MODEL", DEFAULT_MODEL)
        self._routed_model: str | None = None  # used by smart routing

    # ── Smart routing ────────────────────────────────────────────────────────

    def is_smart(self) -> bool:
        return self.model.startswith("smart:")

    def route(self, turn_type: str) -> str:
        """
        Select a concrete model for this turn based on turn_type.
        turn_type: 'thinking' | 'coding' | 'fast' | 'default'
        Returns the actual model id that will be used.
        """
        if not self.is_smart():
            return self.model
        routing = ALL_MODELS[self.model]["routing"]
        concrete = routing.get(turn_type) or routing["default"]
        self._routed_model = concrete
        return concrete

    def route_for_tool(self, tool_name: str | None) -> str:
        """Convenience: pick routing slot from a tool name."""
        if tool_name in CODING_TOOLS:
            return self.route("coding")
        if tool_name in FAST_TOOLS:
            return self.route("fast")
        return self.route("default")

    # ── Main chat entry ──────────────────────────────────────────────────────

    def chat(self, messages: List[Dict[str, str]], turn_type: str = "default") -> str:
        model = self.route(turn_type) if self.is_smart() else self.model
        info = ALL_MODELS.get(model, {})
        provider = info.get("provider", "openrouter")
        try:
            if provider == "groq":
                return self._call_groq(messages, model)
            else:
                return self._call_openrouter(messages, model)
        except Exception as e:
            return f"Error: {str(e)}"

    # ── Groq ─────────────────────────────────────────────────────────────────

    def _call_groq(self, messages: List[Dict[str, str]], model: str) -> str:
        api_key = get_key("groq", "GROQ_API_KEY")
        if not api_key:
            return (
                "CLAW_ERROR:NO_KEY:groq|"
                "No Groq API key found. Get a free key at https://console.groq.com "
                "and add it in the ⚙ Settings panel."
            )
        info = ALL_MODELS.get(model, {})
        groq_id = info.get("groq_id") or model.replace("groq:", "")
        body = {
            "model": groq_id,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 8192,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            result = _post_json("https://api.groq.com/openai/v1/chat/completions", headers, body)
            if "choices" in result:
                content = result["choices"][0]["message"]["content"]
                # DeepSeek R1 distill puts reasoning in <think> tags — strip them
                content = _strip_think_tags(content)
                return content
            return f"Groq API Error: {json.dumps(result)}"
        except urllib.error.HTTPError as e:
            body_text = _read_http_error(e)
            if e.code == 401:
                return (
                    f"CLAW_ERROR:BAD_KEY:groq|"
                    f"Invalid Groq API key (401). Update it in ⚙ Settings. Detail: {body_text[:200]}"
                )
            if e.code == 429:
                return "CLAW_ERROR:RATE_LIMIT:groq|Groq rate limit hit. Try a different model or wait a moment."
            if e.code == 503:
                return "CLAW_ERROR:SERVER_ERROR:groq|Groq service temporarily unavailable. Try again shortly."
            return f"Groq HTTP {e.code}: {body_text[:300]}"
        except Exception as e:
            return f"Groq connection error: {str(e)}"

    # ── OpenRouter ───────────────────────────────────────────────────────────

    def _call_openrouter(self, messages: List[Dict[str, str]], model: str) -> str:
        api_key = get_key("openrouter", "OPENROUTER_API_KEY")
        if not api_key:
            return (
                "CLAW_ERROR:NO_KEY:openrouter|"
                "No OpenRouter API key found. Get a free key at https://openrouter.ai/keys "
                "and add it in the ⚙ Settings panel."
            )
        body = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 8000,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://claw-ide.replit.app",
            "X-Title": "Claw IDE",
            "Content-Type": "application/json",
        }
        try:
            result = _post_json("https://openrouter.ai/api/v1/chat/completions", headers, body)
            if "choices" in result:
                return result["choices"][0]["message"]["content"] or ""
            # Surface OpenRouter error clearly
            err = result.get("error", {})
            return f"CLAW_ERROR:API_ERROR:openrouter|{err.get('message', json.dumps(result)[:200])}"
        except urllib.error.HTTPError as e:
            body_text = _read_http_error(e)
            if e.code == 401:
                return (
                    "CLAW_ERROR:BAD_KEY:openrouter|"
                    "Invalid OpenRouter API key (401). "
                    "Create a fresh key at https://openrouter.ai/keys and add it in ⚙ Settings."
                )
            if e.code == 402:
                return "CLAW_ERROR:NO_CREDITS:openrouter|Insufficient credits. Add credits at https://openrouter.ai or switch to a free model."
            if e.code == 404:
                return (
                    f"CLAW_ERROR:API_ERROR:openrouter|"
                    f"Model endpoint not found (404). The model '{model}' may be unavailable. "
                    f"Please switch to a different model in the sidebar."
                )
            if e.code == 429:
                return "CLAW_ERROR:RATE_LIMIT:openrouter|OpenRouter rate limit hit. Try switching to a different model."
            if e.code == 503:
                return "CLAW_ERROR:SERVER_ERROR:openrouter|OpenRouter service temporarily unavailable. Try again shortly."
            return f"OpenRouter HTTP {e.code}: {body_text[:300]}"
        except Exception as e:
            return f"OpenRouter connection error: {str(e)}"

    # ── Active model info ────────────────────────────────────────────────────

    def get_active_model_info(self) -> dict:
        """Return the catalog entry for the model currently in use."""
        model = self._routed_model or self.model
        return ALL_MODELS.get(model, {})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_http_error(e: urllib.error.HTTPError) -> str:
    try:
        return e.read().decode("utf-8")
    except Exception:
        return ""

def _strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks from DeepSeek-style responses."""
    import re
    return re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()


def validate_key(provider: str, key: str) -> dict:
    """Test an API key. Returns {ok, message}."""
    try:
        if provider == "groq":
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/models",
                headers={
                    "Authorization": f"Bearer {key}",
                    "User-Agent": "Mozilla/5.0 ClawIDE/3.1",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
                count = len(data.get("data", []))
                return {"ok": True, "message": f"Groq key valid — {count} models available"}
        else:
            # Use the /auth/key endpoint — no model call needed, just checks the key
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/auth/key",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
                label = data.get("data", {}).get("label") or "valid"
                credits = data.get("data", {}).get("limit_remaining")
                credit_str = f" · ${credits:.4f} credits" if credits is not None else ""
                return {"ok": True, "message": f"OpenRouter key valid ✓ ({label}{credit_str})"}
    except urllib.error.HTTPError as e:
        body_text = _read_http_error(e)
        if e.code == 401:
            return {"ok": False, "message": f"Invalid key (401 Unauthorized). {body_text[:120]}"}
        if e.code == 402:
            return {"ok": True, "message": "Key valid — add credits for paid models (free models work now)"}
        return {"ok": False, "message": f"HTTP {e.code}: {body_text[:120]}"}
    except Exception as ex:
        return {"ok": False, "message": f"Connection error: {str(ex)}"}


# Backwards-compatible alias
OpenRouterClient = LLMClient
