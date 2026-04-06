import json
import os
import re
import threading
from pathlib import Path
from typing import List, Dict

from openai import OpenAI

# ===================== TIKTOKEN (ACCURATE TOKEN COUNTING) =====================
try:
    import tiktoken
    _TIKTOKEN_ENC = tiktoken.get_encoding("cl100k_base")
    _TIKTOKEN_AVAILABLE = True
except Exception:
    _TIKTOKEN_ENC = None
    _TIKTOKEN_AVAILABLE = False


def count_tokens(text: str) -> int:
    if _TIKTOKEN_AVAILABLE and _TIKTOKEN_ENC is not None:
        try:
            return max(1, len(_TIKTOKEN_ENC.encode(text, disallowed_special=())))
        except Exception:
            pass
    return max(1, len(text) // 4)


# ===================== PROVIDER CONFIG STORE =====================
# All provider keys and base URLs are stored in ~/.config/nexus/providers.json
# so they persist across restarts without touching source code or env vars.

_CONFIG_DIR  = Path.home() / ".config" / "nexus"
_CONFIG_FILE = _CONFIG_DIR / "providers.json"
_config_lock = threading.Lock()

_DEFAULT_PROVIDERS: dict[str, dict] = {
    "nvidia":     {"key": "", "base_url": "https://integrate.api.nvidia.com/v1"},
    "openai":     {"key": "", "base_url": "https://api.openai.com/v1"},
    "openrouter": {"key": "", "base_url": "https://openrouter.ai/api/v1"},
    "groq":       {"key": "", "base_url": "https://api.groq.com/openai/v1"},
    "ollama":     {"key": "ollama", "base_url": "http://localhost:11434/v1"},
    "custom":     {"key": "", "base_url": ""},
}


def _load_config() -> dict:
    try:
        if _CONFIG_FILE.exists():
            data = json.loads(_CONFIG_FILE.read_text())
            merged = {**_DEFAULT_PROVIDERS}
            for p, v in data.items():
                if p in merged:
                    merged[p] = {**merged[p], **v}
                else:
                    merged[p] = v
            return merged
    except Exception:
        pass
    return dict(_DEFAULT_PROVIDERS)


def _save_config(cfg: dict) -> None:
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
        _CONFIG_FILE.chmod(0o600)
    except Exception:
        pass


def get_provider_config(provider: str) -> dict:
    with _config_lock:
        return _load_config().get(provider, {})


def set_provider_config(provider: str, key: str = "", base_url: str = "") -> None:
    with _config_lock:
        cfg = _load_config()
        entry = cfg.get(provider, {})
        if key:
            entry["key"] = key
        if base_url:
            entry["base_url"] = base_url
        cfg[provider] = entry
        _save_config(cfg)
    # Mirror to env vars for backward compat
    if provider == "nvidia" and key:
        os.environ["NVIDIA_API_KEY"] = key
    elif provider == "openai" and key:
        os.environ["OPENAI_API_KEY"] = key
    elif provider == "groq" and key:
        os.environ["GROQ_API_KEY"] = key


def get_all_provider_status() -> dict:
    """Return per-provider configured status + masked key prefix."""
    with _config_lock:
        cfg = _load_config()
    out = {}
    for p, v in cfg.items():
        key = v.get("key", "") or os.environ.get(f"{p.upper()}_API_KEY", "")
        configured = bool(key) and key != "ollama"
        # Ollama needs no real key — configured if base_url is reachable
        if p == "ollama":
            configured = bool(v.get("base_url", ""))
        out[p] = {
            "configured": configured,
            "prefix": key[:14] + "…" if (key and key != "ollama") else ("local" if p == "ollama" else ""),
            "base_url": v.get("base_url", ""),
        }
    return out


# ── Bootstrap env keys into config on first import ───────────────────────────
def _bootstrap_env_keys() -> None:
    _env_map = {
        "nvidia":     "NVIDIA_API_KEY",
        "openai":     "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "groq":       "GROQ_API_KEY",
    }
    with _config_lock:
        cfg = _load_config()
        changed = False
        for provider, env_var in _env_map.items():
            env_val = os.environ.get(env_var, "")
            if env_val and not cfg.get(provider, {}).get("key"):
                cfg.setdefault(provider, {})["key"] = env_val
                changed = True
        if changed:
            _save_config(cfg)

_bootstrap_env_keys()


# ── Legacy helpers (backward compat with app.py imports) ─────────────────────
_KEY_FILE = str(_CONFIG_DIR / "api_key")  # kept for compatibility

def get_nvidia_key() -> str:
    env = os.environ.get("NVIDIA_API_KEY", "")
    if env:
        return env
    return get_provider_config("nvidia").get("key", "")

def set_runtime_key(key: str) -> None:
    set_provider_config("nvidia", key=key.strip())

def _load_persisted_key() -> str:
    return get_nvidia_key()

def _persist_key(key: str) -> None:
    set_provider_config("nvidia", key=key)


# ===================== MODEL CATALOG =====================

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

NVIDIA_MODELS: dict[str, dict] = {
    "nvidia:phi-4-mini-instruct": {
        "label": "Phi-4 Mini Instruct", "short": "Fast & Capable",
        "description": "Microsoft's Phi-4 Mini — compact, fast, and capable for coding and reasoning.",
        "context": 16384, "tier": "free", "provider": "nvidia", "role": "balanced",
        "emoji": "⚡", "price_note": "Free", "nvidia_id": "microsoft/phi-4-mini-instruct",
    },
    "nvidia:llama-3.3-70b-instruct": {
        "label": "Llama 3.3 70B Instruct", "short": "Best All-Rounder",
        "description": "Meta's Llama 3.3 70B — excellent at coding, reasoning, and instruction-following.",
        "context": 128000, "tier": "free", "provider": "nvidia", "role": "balanced",
        "emoji": "⚡", "price_note": "Free", "nvidia_id": "meta/llama-3.3-70b-instruct",
    },
    "nvidia:llama-3.1-8b-instruct": {
        "label": "Llama 3.1 8B Instruct", "short": "Ultra-Fast",
        "description": "Meta's Llama 3.1 8B — lightning-fast for quick edits and simple tasks.",
        "context": 128000, "tier": "free", "provider": "nvidia", "role": "fast",
        "emoji": "⚡", "price_note": "Free", "nvidia_id": "meta/llama-3.1-8b-instruct",
    },
    "nvidia:deepseek-r1-distill-llama-70b": {
        "label": "DeepSeek R1 Distill 70B", "short": "Fast Thinker",
        "description": "DeepSeek's chain-of-thought reasoning model distilled into Llama 70B.",
        "context": 128000, "tier": "free", "provider": "nvidia", "role": "thinking",
        "emoji": "🧠", "price_note": "Free", "nvidia_id": "deepseek-ai/deepseek-r1-distill-llama-70b",
    },
    "nvidia:qwen2.5-coder-32b": {
        "label": "Qwen2.5 Coder 32B", "short": "Code Specialist",
        "description": "Alibaba's Qwen2.5 Coder — purpose-built for code generation and debugging.",
        "context": 32768, "tier": "free", "provider": "nvidia", "role": "coding",
        "emoji": "💻", "price_note": "Free", "nvidia_id": "qwen/qwen2.5-coder-32b-instruct",
    },
    "nvidia:nemotron-super-49b": {
        "label": "Nemotron Super 49B", "short": "NVIDIA Flagship",
        "description": "NVIDIA's Nemotron Super 49B — high-quality for complex tasks and long-form generation.",
        "context": 32768, "tier": "free", "provider": "nvidia", "role": "powerful",
        "emoji": "🚀", "price_note": "Free", "nvidia_id": "nvidia/llama-3.3-nemotron-super-49b-v1",
    },
    "nvidia:mistral-small-4": {
        "label": "Mistral Small 4", "short": "Advanced Coding",
        "description": "Mistral Small 4 (119B) — advanced model for complex coding tasks.",
        "context": 32768, "tier": "free", "provider": "nvidia", "role": "coding",
        "emoji": "💻", "price_note": "Free", "nvidia_id": "mistralai/mistral-small-4-119b-2603",
    },
    "nvidia:gemma-3-12b": {
        "label": "Gemma 3 12B", "short": "Google Balanced",
        "description": "Google's Gemma 3 12B — well-rounded, fast, and capable for most coding tasks.",
        "context": 131072, "tier": "free", "provider": "nvidia", "role": "balanced",
        "emoji": "⚡", "price_note": "Free", "nvidia_id": "google/gemma-3-12b-it",
    },
    "nvidia:minimax-m2.5": {
        "label": "MiniMax M2.5", "short": "Long Context Powerhouse",
        "description": "MiniMax M2.5 — large-scale model with exceptional long-context understanding.",
        "context": 1000000, "tier": "free", "provider": "nvidia", "role": "powerful",
        "emoji": "🚀", "price_note": "Free", "nvidia_id": "minimaxai/minimax-m2.5",
        "temperature": 1, "top_p": 0.95,
    },
    "nvidia:nemotron-3-super-120b": {
        "label": "Nemotron-3 Super 120B", "short": "Adv. Professional Coding",
        "description": "Nemotron-3 Super 120B — advanced professional model for enterprise-grade coding.",
        "context": 32768, "tier": "free", "provider": "nvidia", "role": "coding",
        "emoji": "💻", "price_note": "Free", "nvidia_id": "nvidia/nemotron-3-super-120b-a12b",
    },
}

OPENAI_MODELS: dict[str, dict] = {
    "openai:gpt-4o": {
        "label": "GPT-4o", "short": "OpenAI Flagship",
        "description": "OpenAI's most capable multimodal model — excellent at reasoning and code.",
        "context": 128000, "tier": "paid", "provider": "openai", "role": "powerful",
        "emoji": "🌟", "price_note": "Paid", "model_id": "gpt-4o",
    },
    "openai:gpt-4o-mini": {
        "label": "GPT-4o Mini", "short": "Fast & Affordable",
        "description": "GPT-4o Mini — fast and cost-effective for everyday coding tasks.",
        "context": 128000, "tier": "paid", "provider": "openai", "role": "fast",
        "emoji": "⚡", "price_note": "Paid", "model_id": "gpt-4o-mini",
    },
    "openai:o3-mini": {
        "label": "o3-mini", "short": "Reasoning Specialist",
        "description": "OpenAI's o3-mini — compact reasoning model for complex problem solving.",
        "context": 200000, "tier": "paid", "provider": "openai", "role": "thinking",
        "emoji": "🧠", "price_note": "Paid", "model_id": "o3-mini",
    },
}

OPENROUTER_MODELS: dict[str, dict] = {
    "openrouter:anthropic/claude-3.5-sonnet": {
        "label": "Claude 3.5 Sonnet", "short": "Anthropic Best",
        "description": "Anthropic's Claude 3.5 Sonnet — superb coding, analysis, and long-context tasks.",
        "context": 200000, "tier": "paid", "provider": "openrouter", "role": "powerful",
        "emoji": "🧠", "price_note": "Paid", "model_id": "anthropic/claude-3.5-sonnet",
    },
    "openrouter:anthropic/claude-3-haiku": {
        "label": "Claude 3 Haiku", "short": "Anthropic Fast",
        "description": "Claude 3 Haiku — ultra-fast and affordable with strong coding performance.",
        "context": 200000, "tier": "paid", "provider": "openrouter", "role": "fast",
        "emoji": "⚡", "price_note": "Paid", "model_id": "anthropic/claude-3-haiku",
    },
    "openrouter:google/gemini-2.0-flash-thinking-exp": {
        "label": "Gemini 2.0 Flash Thinking", "short": "Google Reasoning",
        "description": "Google's Gemini 2.0 Flash with extended thinking — great for complex reasoning.",
        "context": 1000000, "tier": "free", "provider": "openrouter", "role": "thinking",
        "emoji": "🧠", "price_note": "Free*", "model_id": "google/gemini-2.0-flash-thinking-exp",
    },
    "openrouter:mistralai/mistral-7b-instruct": {
        "label": "Mistral 7B Instruct", "short": "Fast Open Source",
        "description": "Mistral 7B — fast, lightweight, and free via OpenRouter.",
        "context": 32768, "tier": "free", "provider": "openrouter", "role": "fast",
        "emoji": "⚡", "price_note": "Free*", "model_id": "mistralai/mistral-7b-instruct",
    },
    "openrouter:deepseek/deepseek-r1": {
        "label": "DeepSeek R1", "short": "Deep Reasoner",
        "description": "DeepSeek R1 full model — state-of-the-art chain-of-thought reasoning.",
        "context": 65536, "tier": "paid", "provider": "openrouter", "role": "thinking",
        "emoji": "🧠", "price_note": "Paid", "model_id": "deepseek/deepseek-r1",
    },
}

GROQ_MODELS: dict[str, dict] = {
    "groq:llama-3.3-70b-versatile": {
        "label": "Llama 3.3 70B (Groq)", "short": "Fastest 70B",
        "description": "Llama 3.3 70B running on Groq's LPU — extremely fast inference.",
        "context": 128000, "tier": "free", "provider": "groq", "role": "fast",
        "emoji": "⚡", "price_note": "Free*", "model_id": "llama-3.3-70b-versatile",
    },
    "groq:llama-3.1-8b-instant": {
        "label": "Llama 3.1 8B (Groq)", "short": "Ultra-Fast Groq",
        "description": "Llama 3.1 8B on Groq LPU — the fastest option for simple tasks.",
        "context": 128000, "tier": "free", "provider": "groq", "role": "fast",
        "emoji": "⚡", "price_note": "Free*", "model_id": "llama-3.1-8b-instant",
    },
    "groq:deepseek-r1-distill-llama-70b": {
        "label": "DeepSeek R1 70B (Groq)", "short": "Fast Thinker Groq",
        "description": "DeepSeek R1 reasoning model running on Groq's ultra-fast LPU hardware.",
        "context": 128000, "tier": "free", "provider": "groq", "role": "thinking",
        "emoji": "🧠", "price_note": "Free*", "model_id": "deepseek-r1-distill-llama-70b",
    },
    "groq:qwen-2.5-coder-32b": {
        "label": "Qwen 2.5 Coder 32B (Groq)", "short": "Fast Code Groq",
        "description": "Qwen 2.5 Coder on Groq LPU — fast code generation and debugging.",
        "context": 32768, "tier": "free", "provider": "groq", "role": "coding",
        "emoji": "💻", "price_note": "Free*", "model_id": "qwen-2.5-coder-32b",
    },
}

# Ollama models are discovered dynamically — these are popular defaults
# shown when Ollama hasn't been queried yet or has no models installed.
OLLAMA_PRESET_MODELS: dict[str, dict] = {
    "ollama:llama3.2": {
        "label": "Llama 3.2 (Ollama)", "short": "Local Llama",
        "description": "Meta's Llama 3.2 running locally via Ollama. Pull with: ollama pull llama3.2",
        "context": 128000, "tier": "local", "provider": "ollama", "role": "balanced",
        "emoji": "🏠", "price_note": "Local", "model_id": "llama3.2",
    },
    "ollama:codellama": {
        "label": "Code Llama (Ollama)", "short": "Local Coder",
        "description": "Meta's Code Llama running locally — specialized for code generation.",
        "context": 16384, "tier": "local", "provider": "ollama", "role": "coding",
        "emoji": "💻", "price_note": "Local", "model_id": "codellama",
    },
    "ollama:deepseek-coder-v2": {
        "label": "DeepSeek Coder V2 (Ollama)", "short": "Local DeepSeek",
        "description": "DeepSeek Coder V2 running locally — excellent code generation.",
        "context": 32768, "tier": "local", "provider": "ollama", "role": "coding",
        "emoji": "💻", "price_note": "Local", "model_id": "deepseek-coder-v2",
    },
    "ollama:qwen2.5-coder": {
        "label": "Qwen 2.5 Coder (Ollama)", "short": "Local Qwen Coder",
        "description": "Qwen 2.5 Coder running locally via Ollama.",
        "context": 32768, "tier": "local", "provider": "ollama", "role": "coding",
        "emoji": "💻", "price_note": "Local", "model_id": "qwen2.5-coder",
    },
    "ollama:mistral": {
        "label": "Mistral (Ollama)", "short": "Local Mistral",
        "description": "Mistral 7B running locally via Ollama — fast and general purpose.",
        "context": 32768, "tier": "local", "provider": "ollama", "role": "balanced",
        "emoji": "🏠", "price_note": "Local", "model_id": "mistral",
    },
    "ollama:phi4-mini": {
        "label": "Phi-4 Mini (Ollama)", "short": "Local Phi",
        "description": "Microsoft Phi-4 Mini running locally — compact and capable.",
        "context": 16384, "tier": "local", "provider": "ollama", "role": "fast",
        "emoji": "⚡", "price_note": "Local", "model_id": "phi4-mini",
    },
}

ALL_MODELS: dict[str, dict] = {
    **NVIDIA_MODELS,
    **OPENAI_MODELS,
    **OPENROUTER_MODELS,
    **GROQ_MODELS,
    **OLLAMA_PRESET_MODELS,
}

DEFAULT_MODEL = "nvidia:phi-4-mini-instruct"

CODING_TOOLS = {"FileEditTool", "BashTool"}
FAST_TOOLS   = {"ListDirTool", "FileReadTool", "ViewFileLinesTool"}


def get_all_models() -> dict[str, dict]:
    return ALL_MODELS


def get_ollama_models(base_url: str = "") -> list[dict]:
    """
    Query a running Ollama instance and return its installed models
    formatted as NEXUS model entries. Returns [] on any error.
    """
    import urllib.request
    url = (base_url or get_provider_config("ollama").get("base_url", "http://localhost:11434/v1"))
    # Ollama native API is at /api/tags (not the OpenAI-compat path)
    tags_url = url.replace("/v1", "").rstrip("/") + "/api/tags"
    try:
        req = urllib.request.urlopen(tags_url, timeout=3)
        data = json.loads(req.read())
        models = data.get("models", [])
        result = []
        for m in models:
            name = m.get("name", "")
            if not name:
                continue
            mid = f"ollama:{name}"
            result.append({
                "id":          mid,
                "label":       f"{name} (Ollama)",
                "short":       "Local Model",
                "description": f"{name} running locally via Ollama.",
                "context":     m.get("details", {}).get("parameter_size") and 32768 or 32768,
                "tier":        "local",
                "provider":    "ollama",
                "role":        "balanced",
                "emoji":       "🏠",
                "price_note":  "Local",
                "model_id":    name,
            })
        return result
    except Exception:
        return []


# ===================== PROVIDER ROUTING =====================

def _resolve_model(model_id: str) -> tuple[str, str, str, dict]:
    """
    Given a full model ID like 'nvidia:phi-4-mini-instruct', return:
      (provider, real_model_id, base_url, info_dict)
    """
    if ":" not in model_id:
        model_id = f"nvidia:{model_id}"

    provider, _, raw_id = model_id.partition(":")
    info = ALL_MODELS.get(model_id, {})

    cfg = get_provider_config(provider)
    base_url = cfg.get("base_url", "")

    # Determine the actual model name to send to the API
    actual_id = (
        info.get("nvidia_id")    # NVIDIA uses different internal IDs
        or info.get("model_id")  # All others use model_id directly
        or raw_id
    )

    return provider, actual_id, base_url, info


def _get_api_key(provider: str) -> str:
    """Return the API key for a provider, checking env vars then config file."""
    env_map = {
        "nvidia":     "NVIDIA_API_KEY",
        "openai":     "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "groq":       "GROQ_API_KEY",
    }
    env_key = os.environ.get(env_map.get(provider, ""), "")
    if env_key:
        return env_key
    return get_provider_config(provider).get("key", "")


# ===================== LLM CLIENT =====================

class LLMClient:
    """
    Universal LLM client supporting NVIDIA, OpenAI, OpenRouter, Groq,
    Ollama (local), and any OpenAI-compatible custom API.

    Model IDs use a 'provider:model_name' format:
      nvidia:phi-4-mini-instruct
      ollama:llama3.2
      openai:gpt-4o
      openrouter:anthropic/claude-3.5-sonnet
      groq:llama-3.3-70b-versatile
      custom:my-model
    """

    def __init__(self, model: str = None):
        self.model = model or os.environ.get("CLAW_MODEL", DEFAULT_MODEL)

    def is_smart(self) -> bool:
        return False

    def _count_tokens(self, text: str) -> int:
        return count_tokens(text)

    def _get_safe_max_tokens(self, messages: list, ctx_limit: int, desired: int = 4096) -> int:
        total_input = sum(self._count_tokens(m.get("content", "")) for m in messages)
        remaining = ctx_limit - total_input - 300
        return min(desired, max(512, remaining))

    def _trim_messages(self, messages: list, ctx_limit: int, completion_budget: int = 4096) -> list:
        max_input = ctx_limit - completion_budget - 500
        system_msgs = [m for m in messages if m.get("role") == "system"]
        other_msgs  = [m for m in messages if m.get("role") != "system"]

        while True:
            total = sum(self._count_tokens(m.get("content", "")) for m in system_msgs + other_msgs)
            if total <= max_input or len(other_msgs) <= 1:
                break
            other_msgs.pop(0)

        total = sum(self._count_tokens(m.get("content", "")) for m in system_msgs + other_msgs)
        if total > max_input and other_msgs:
            m = other_msgs[0]
            sys_toks = sum(self._count_tokens(s.get("content", "")) for s in system_msgs)
            target = max_input - sys_toks
            if target > 100:
                content = m.get("content", "")
                char_lim = target * 4
                m["content"] = content[:char_lim] + "... [TRUNCATED]"
        return system_msgs + other_msgs

    def _make_client(self, provider: str, base_url: str, api_key: str) -> OpenAI:
        """Build an OpenAI-compatible client for any provider."""
        headers = {}
        if provider == "openrouter":
            headers = {
                "HTTP-Referer": "https://nexus-ide.local",
                "X-Title": "NEXUS IDE",
            }
        return OpenAI(
            base_url=base_url,
            api_key=api_key or "no-key",
            default_headers=headers if headers else None,
        )

    def _error_message(self, provider: str, err: str, model_id: str) -> str:
        if "401" in err or "unauthorized" in err.lower():
            return f"CLAW_ERROR:BAD_KEY:{provider}|Invalid {provider.upper()} API key (401). Update it in ⚙ Settings."
        if "403" in err or "forbidden" in err.lower():
            return f"CLAW_ERROR:BAD_KEY:{provider}|Access forbidden (403). Check your {provider.upper()} key permissions."
        if "429" in err or "rate" in err.lower():
            return f"CLAW_ERROR:RATE_LIMIT:{provider}|{provider.upper()} rate limit hit. Try again in a moment."
        if "404" in err:
            return f"CLAW_ERROR:API_ERROR:{provider}|Model not found: '{model_id}'. Check the model name."
        if "connection" in err.lower() or "refused" in err.lower():
            if provider == "ollama":
                return (
                    "CLAW_ERROR:CONNECTION:ollama|Cannot connect to Ollama. "
                    "Make sure Ollama is running: https://ollama.com"
                )
            return f"CLAW_ERROR:CONNECTION:{provider}|Connection failed. Check your network and API endpoint."
        return f"{provider.upper()} API error: {err}"

    def chat(self, messages: List[Dict[str, str]], turn_type: str = "default") -> str:
        provider, actual_id, base_url, info = _resolve_model(self.model)
        api_key = _get_api_key(provider)

        if provider != "ollama" and not api_key:
            return (
                f"CLAW_ERROR:NO_KEY:{provider}|"
                f"No {provider.upper()} API key configured. Add it in ⚙ Settings → API Keys."
            )

        ctx_limit = info.get("context", 32768)
        messages  = self._trim_messages(messages, ctx_limit, 4096)
        max_tok   = self._get_safe_max_tokens(messages, ctx_limit, 4096)
        temp      = info.get("temperature", 0.2)
        top_p     = info.get("top_p", 0.7)

        try:
            client     = self._make_client(provider, base_url, api_key)
            completion = client.chat.completions.create(
                model=actual_id, messages=messages,
                temperature=temp, top_p=top_p,
                max_tokens=max_tok, stream=True,
            )
            result = ""
            for chunk in completion:
                if not getattr(chunk, "choices", None):
                    continue
                delta = chunk.choices[0].delta
                if delta.content is not None:
                    result += delta.content
            return _strip_think_tags(result)
        except Exception as e:
            return self._error_message(provider, str(e), actual_id)

    def chat_stream(self, messages: List[Dict[str, str]], turn_type: str = "default"):
        provider, actual_id, base_url, info = _resolve_model(self.model)
        api_key = _get_api_key(provider)

        if provider != "ollama" and not api_key:
            yield (
                f"CLAW_ERROR:NO_KEY:{provider}|"
                f"No {provider.upper()} API key configured. Add it in ⚙ Settings → API Keys."
            )
            return

        ctx_limit = info.get("context", 32768)
        messages  = self._trim_messages(messages, ctx_limit, 4096)
        max_tok   = self._get_safe_max_tokens(messages, ctx_limit, 4096)
        temp      = info.get("temperature", 0.2)
        top_p     = info.get("top_p", 0.7)

        try:
            client     = self._make_client(provider, base_url, api_key)
            completion = client.chat.completions.create(
                model=actual_id, messages=messages,
                temperature=temp, top_p=top_p,
                max_tokens=max_tok, stream=True,
            )
            for chunk in completion:
                if not getattr(chunk, "choices", None):
                    continue
                delta = chunk.choices[0].delta
                if delta.content is not None:
                    yield delta.content
        except Exception as e:
            yield self._error_message(provider, str(e), actual_id)

    def get_active_model_info(self) -> dict:
        return ALL_MODELS.get(self.model, {})

    def route(self, turn_type: str) -> str:
        return self.model

    def route_for_tool(self, tool_name: str | None) -> str:
        return self.model


# ===================== HELPERS =====================

def _strip_think_tags(text: str) -> str:
    return re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()


def validate_key(key: str, provider: str = "nvidia") -> dict:
    """Test an API key by listing available models from the provider."""
    try:
        _, _, base_url, _ = _resolve_model(f"{provider}:{provider}")
        if not base_url:
            base_url = _DEFAULT_PROVIDERS.get(provider, {}).get("base_url", NVIDIA_BASE_URL)
        client = OpenAI(base_url=base_url, api_key=key)
        models = client.models.list()
        count  = len(list(models))
        return {"ok": True, "message": f"{provider.upper()} key valid ✓ — {count} models available"}
    except Exception as e:
        err = str(e)
        if "401" in err or "unauthorized" in err.lower():
            return {"ok": False, "message": f"Invalid key (401 Unauthorized). Check your {provider.upper()} API key."}
        return {"ok": False, "message": f"Connection error: {err[:120]}"}


def validate_ollama(base_url: str = "") -> dict:
    """Check if an Ollama instance is reachable and return its model count."""
    models = get_ollama_models(base_url)
    if models:
        return {"ok": True, "message": f"Ollama connected ✓ — {len(models)} model(s) installed", "models": models}
    return {"ok": False, "message": "Cannot connect to Ollama. Make sure it's running: https://ollama.com"}


# Backwards-compatible aliases
OpenRouterClient = LLMClient
SMART_MODELS = {}

def set_runtime_key_compat(provider: str, key: str):
    set_provider_config(provider, key=key)

def refresh_all_models():
    return ALL_MODELS, None

def get_key(provider: str, env_var: str) -> str:
    return _get_api_key(provider)

def _or_model_cache():
    return {"fetched_at": 0, "error": None}
