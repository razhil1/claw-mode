import os
import json
import time
import threading
import urllib.request
import urllib.error
from typing import List, Dict

# ===================== RUNTIME KEY STORE =====================
_runtime_keys: dict[str, str] = {}

def set_runtime_key(provider: str, key: str):
    _runtime_keys[provider] = key

def get_key(provider: str, env_var: str) -> str:
    return _runtime_keys.get(provider) or os.environ.get(env_var, "")


# ===================== GROQ STATIC MODELS (always available) =====================
GROQ_MODELS: dict[str, dict] = {
    "groq:llama-3.3-70b-versatile": {
        "label": "Llama 3.3 70B Versatile",
        "short": "Best All-Rounder",
        "description": "Meta's best open-source model on ultra-fast Groq hardware. Excellent at coding, reasoning, and instruction-following.",
        "context": 128000, "tier": "free", "provider": "groq",
        "groq_id": "llama-3.3-70b-versatile",
        "role": "balanced",
        "emoji": "⚡",
        "price_note": "Free",
    },
    "groq:deepseek-r1-distill-llama-70b": {
        "label": "DeepSeek R1 Distill 70B",
        "short": "Fast Thinker",
        "description": "DeepSeek's reasoning model distilled into Llama 70B, served on Groq for very fast chain-of-thought.",
        "context": 128000, "tier": "free", "provider": "groq",
        "groq_id": "deepseek-r1-distill-llama-70b",
        "role": "thinking",
        "emoji": "🧠",
        "price_note": "Free",
    },
    "groq:llama-3.1-8b-instant": {
        "label": "Llama 3.1 8B Instant",
        "short": "Ultra-Fast",
        "description": "Lightning-fast small model on Groq. Ideal for quick edits, refactoring, and simple tasks.",
        "context": 128000, "tier": "free", "provider": "groq",
        "groq_id": "llama-3.1-8b-instant",
        "role": "fast",
        "emoji": "⚡",
        "price_note": "Free",
    },
    "groq:gemma2-9b-it": {
        "label": "Gemma 2 9B",
        "short": "Balanced",
        "description": "Google's Gemma 2 on Groq. Efficient and capable for most coding tasks.",
        "context": 8192, "tier": "free", "provider": "groq",
        "groq_id": "gemma2-9b-it",
        "role": "balanced",
        "emoji": "⚡",
        "price_note": "Free",
    },
    "groq:mixtral-8x7b-32768": {
        "label": "Mixtral 8x7B MoE",
        "short": "MoE Model",
        "description": "Mistral's sparse mixture-of-experts model. Strong instruction following with 32K context.",
        "context": 32768, "tier": "free", "provider": "groq",
        "groq_id": "mixtral-8x7b-32768",
        "role": "balanced",
        "emoji": "⚡",
        "price_note": "Free",
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
        "price_note": "Free",
        "routing": {
            "thinking": "groq:deepseek-r1-distill-llama-70b",
            "coding":   "groq:llama-3.3-70b-versatile",
            "fast":     "groq:llama-3.1-8b-instant",
            "default":  "groq:llama-3.3-70b-versatile",
        },
    },
}

DEFAULT_MODEL = "groq:llama-3.3-70b-versatile"

# Tools that warrant using the coding-specialist model
CODING_TOOLS = {"FileEditTool", "BashTool"}
# Tools that can use the fast model
FAST_TOOLS = {"ListDirTool", "FileReadTool", "ViewFileLinesTool"}


# ===================== LIVE OPENROUTER FREE MODEL DETECTION =====================
_or_cache_lock = threading.Lock()
_or_model_cache: dict = {
    "models": {},       # model_id -> info dict
    "fetched_at": 0.0,  # unix timestamp
    "error": None,      # last error message if any
}
CACHE_TTL = 1800  # 30 minutes


def _is_free_model(m: dict) -> bool:
    """Return True if an OpenRouter model entry is genuinely free (zero cost)."""
    mid = m.get("id", "")
    # Models ending in :free are explicitly marked as free tier
    if mid.endswith(":free"):
        return True
    # Also check actual pricing from the API
    pricing = m.get("pricing") or {}
    try:
        prompt_cost = float(pricing.get("prompt", "1") or "1")
        completion_cost = float(pricing.get("completion", "1") or "1")
        return prompt_cost == 0.0 and completion_cost == 0.0
    except (ValueError, TypeError):
        return False


def _or_model_to_info(m: dict) -> dict:
    """Convert a raw OpenRouter model entry to our internal info dict."""
    mid = m.get("id", "")
    name = m.get("name") or mid.split("/")[-1].replace("-", " ").title()
    ctx = m.get("context_length") or 4096

    # Guess role from name/id keywords
    mid_lower = mid.lower()
    name_lower = name.lower()
    if any(k in mid_lower for k in ("r1", "think", "reason", "o1", "o3", "qwq")):
        role = "thinking"
        emoji = "🧠"
    elif any(k in mid_lower for k in ("coder", "code", "devstral", "codestral", "starcoder", "deepseek-coder")):
        role = "coding"
        emoji = "💻"
    elif any(k in mid_lower for k in ("405b", "120b", "72b", "70b", "large", "pro", "opus")):
        role = "powerful"
        emoji = "✦"
    elif any(k in mid_lower for k in ("7b", "8b", "small", "mini", "instant", "fast", "flash", "turbo")):
        role = "fast"
        emoji = "✦"
    else:
        role = "balanced"
        emoji = "✦"

    # Build a human-readable description
    ctx_k = f"{ctx // 1000}K" if ctx >= 1000 else str(ctx)
    description = m.get("description") or f"{name} — {ctx_k} context window. Available free on OpenRouter."

    return {
        "label": name,
        "short": f"{ctx_k} ctx",
        "description": description,
        "context": ctx,
        "tier": "free",
        "provider": "openrouter",
        "role": role,
        "emoji": emoji,
        "price_note": "Free",
    }


def fetch_openrouter_free_models(api_key: str = "", force: bool = False) -> dict[str, dict]:
    """
    Fetch the live list of free models from OpenRouter.
    Results are cached for CACHE_TTL seconds.
    Returns a dict of model_id -> info, or {} on failure.
    """
    with _or_cache_lock:
        age = time.time() - _or_model_cache["fetched_at"]
        if not force and _or_model_cache["fetched_at"] > 0 and age < CACHE_TTL:
            return _or_model_cache["models"]

    # Fetch outside the lock so we don't block callers
    headers = {"Content-Type": "application/json", "User-Agent": "ClawIDE/3.2"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode("utf-8"))

        models_raw = raw.get("data", [])
        free_models: dict[str, dict] = {}
        for m in models_raw:
            if _is_free_model(m):
                mid = m.get("id", "")
                if not mid:
                    continue
                # Ensure the model id ends with :free (normalise)
                if not mid.endswith(":free"):
                    mid = mid + ":free"
                    m["id"] = mid
                free_models[mid] = _or_model_to_info(m)

        with _or_cache_lock:
            _or_model_cache["models"] = free_models
            _or_model_cache["fetched_at"] = time.time()
            _or_model_cache["error"] = None

        return free_models

    except Exception as exc:
        with _or_cache_lock:
            _or_model_cache["error"] = str(exc)
            # Return whatever we had before (may be empty)
            return _or_model_cache["models"]


def get_all_models(or_key: str = "") -> dict[str, dict]:
    """
    Return the full model catalog:
      • Smart combo models (always)
      • Groq free models (always)
      • OpenRouter free models fetched live (with cache)
    """
    or_free = fetch_openrouter_free_models(api_key=or_key or get_key("openrouter", "OPENROUTER_API_KEY"))
    return {
        **SMART_MODELS,
        **GROQ_MODELS,
        **or_free,
    }


# Initial module-level snapshot (used for backwards compatibility)
ALL_MODELS: dict[str, dict] = {
    **SMART_MODELS,
    **GROQ_MODELS,
}


def refresh_all_models() -> tuple[dict, str | None]:
    """Force-refresh OpenRouter free models. Returns (models_dict, error_or_None)."""
    or_key = get_key("openrouter", "OPENROUTER_API_KEY")
    free = fetch_openrouter_free_models(api_key=or_key, force=True)
    err = _or_model_cache.get("error")
    return {**SMART_MODELS, **GROQ_MODELS, **free}, err


# ===================== HTTP HELPERS =====================

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
    """

    def __init__(self, model: str = None):
        self.model = model or os.environ.get("CLAW_MODEL", DEFAULT_MODEL)
        self._routed_model: str | None = None

    def is_smart(self) -> bool:
        return self.model.startswith("smart:")

    def _current_models(self) -> dict:
        return get_all_models()

    def route(self, turn_type: str) -> str:
        if not self.is_smart():
            return self.model
        routing = SMART_MODELS[self.model]["routing"]
        concrete = routing.get(turn_type) or routing["default"]
        self._routed_model = concrete
        return concrete

    def route_for_tool(self, tool_name: str | None) -> str:
        if tool_name in CODING_TOOLS:
            return self.route("coding")
        if tool_name in FAST_TOOLS:
            return self.route("fast")
        return self.route("default")

    def chat(self, messages: List[Dict[str, str]], turn_type: str = "default") -> str:
        model = self.route(turn_type) if self.is_smart() else self.model
        all_models = self._current_models()
        info = all_models.get(model, {})
        provider = info.get("provider", "openrouter")
        try:
            if provider == "groq":
                return self._call_groq(messages, model)
            else:
                return self._call_openrouter(messages, model)
        except Exception as e:
            return f"Error: {str(e)}"

    def _call_groq(self, messages: List[Dict[str, str]], model: str) -> str:
        api_key = get_key("groq", "GROQ_API_KEY")
        if not api_key:
            return (
                "CLAW_ERROR:NO_KEY:groq|"
                "No Groq API key found. Get a free key at https://console.groq.com "
                "and add it in the ⚙ Settings panel."
            )
        all_models = self._current_models()
        info = all_models.get(model, {})
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

    def get_active_model_info(self) -> dict:
        model = self._routed_model or self.model
        return get_all_models().get(model, {})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_http_error(e: urllib.error.HTTPError) -> str:
    try:
        return e.read().decode("utf-8")
    except Exception:
        return ""

def _strip_think_tags(text: str) -> str:
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
                    "User-Agent": "Mozilla/5.0 ClawIDE/3.2",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
                count = len(data.get("data", []))
                return {"ok": True, "message": f"Groq key valid — {count} models available"}
        else:
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
