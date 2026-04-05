import os
import re
import threading
from typing import List, Dict
from openai import OpenAI

# ===================== RUNTIME KEY STORE =====================
_runtime_keys: dict[str, str] = {}
_runtime_keys_lock = threading.Lock()

def set_runtime_key(key: str):
    with _runtime_keys_lock:
        _runtime_keys["nvidia"] = key

def get_nvidia_key() -> str:
    with _runtime_keys_lock:
        return _runtime_keys.get("nvidia") or os.environ.get("NVIDIA_API_KEY", "")


# ===================== NVIDIA MODEL CATALOG =====================
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

NVIDIA_MODELS: dict[str, dict] = {
    "nvidia:phi-4-mini-instruct": {
        "label": "Phi-4 Mini Instruct",
        "short": "Fast & Capable",
        "description": "Microsoft's Phi-4 Mini — compact, fast, and surprisingly capable for coding and reasoning tasks.",
        "context": 16384,
        "tier": "free",
        "provider": "nvidia",
        "role": "balanced",
        "emoji": "⚡",
        "price_note": "Free",
        "nvidia_id": "microsoft/phi-4-mini-instruct",
    },
    "nvidia:llama-3.3-70b-instruct": {
        "label": "Llama 3.3 70B Instruct",
        "short": "Best All-Rounder",
        "description": "Meta's Llama 3.3 70B — excellent at coding, reasoning, and instruction-following.",
        "context": 128000,
        "tier": "free",
        "provider": "nvidia",
        "role": "balanced",
        "emoji": "⚡",
        "price_note": "Free",
        "nvidia_id": "meta/llama-3.3-70b-instruct",
    },
    "nvidia:llama-3.1-8b-instruct": {
        "label": "Llama 3.1 8B Instruct",
        "short": "Ultra-Fast",
        "description": "Meta's Llama 3.1 8B — lightning-fast for quick edits and simple tasks.",
        "context": 128000,
        "tier": "free",
        "provider": "nvidia",
        "role": "fast",
        "emoji": "⚡",
        "price_note": "Free",
        "nvidia_id": "meta/llama-3.1-8b-instruct",
    },
    "nvidia:deepseek-r1-distill-llama-70b": {
        "label": "DeepSeek R1 Distill 70B",
        "short": "Fast Thinker",
        "description": "DeepSeek's chain-of-thought reasoning model distilled into Llama 70B. Best for complex problem solving.",
        "context": 128000,
        "tier": "free",
        "provider": "nvidia",
        "role": "thinking",
        "emoji": "🧠",
        "price_note": "Free",
        "nvidia_id": "deepseek-ai/deepseek-r1-distill-llama-70b",
    },
    "nvidia:qwen2.5-coder-32b": {
        "label": "Qwen2.5 Coder 32B",
        "short": "Code Specialist",
        "description": "Alibaba's Qwen2.5 Coder — purpose-built for code generation and debugging.",
        "context": 32768,
        "tier": "free",
        "provider": "nvidia",
        "role": "coding",
        "emoji": "💻",
        "price_note": "Free",
        "nvidia_id": "qwen/qwen2.5-coder-32b-instruct",
    },
    "nvidia:nemotron-super-49b": {
        "label": "Nemotron Super 49B",
        "short": "NVIDIA Flagship",
        "description": "NVIDIA's Nemotron Super 49B — high-quality model for complex tasks and long-form generation.",
        "context": 32768,
        "tier": "free",
        "provider": "nvidia",
        "role": "powerful",
        "emoji": "🚀",
        "price_note": "Free",
        "nvidia_id": "nvidia/llama-3.3-nemotron-super-49b-v1",
    },
    "nvidia:mistral-small-4": {
        "label": "Mistral Small 4",
        "short": "Advanced Coding",
        "description": "Mistral Small 4 (119B) — an advanced model for complex coding tasks.",
        "context": 32768,
        "tier": "free",
        "provider": "nvidia",
        "role": "coding",
        "emoji": "💻",
        "price_note": "Free",
        "nvidia_id": "mistralai/mistral-small-4-119b-2603",
    },
    "nvidia:gemma-3-12b": {
        "label": "Gemma 3 12B",
        "short": "Google Balanced",
        "description": "Google's Gemma 3 12B — well-rounded, fast, and capable for most coding tasks.",
        "context": 131072,
        "tier": "free",
        "provider": "nvidia",
        "role": "balanced",
        "emoji": "⚡",
        "price_note": "Free",
        "nvidia_id": "google/gemma-3-12b-it",
    },
    "nvidia:minimax-m2.5": {
        "label": "MiniMax M2.5",
        "short": "Long Context Powerhouse",
        "description": "MiniMax M2.5 — large-scale model with exceptional long-context understanding and generation.",
        "context": 1000000,
        "tier": "free",
        "provider": "nvidia",
        "role": "powerful",
        "emoji": "🚀",
        "price_note": "Free",
        "nvidia_id": "minimaxai/minimax-m2.5",
        "temperature": 1,
        "top_p": 0.95,
    },
    "nvidia:nemotron-3-super-120b": {
        "label": "Nemotron-3 Super 120B",
        "short": "Adv. Professional Coding",
        "description": "Nemotron-3 Super 120B — Advanced professional model tuned for complex reasoning and enterprise-grade coding.",
        "context": 32768,
        "tier": "free",
        "provider": "nvidia",
        "role": "coding",
        "emoji": "💻",
        "price_note": "Free",
        "nvidia_id": "nvidia/nemotron-3-super-120b-a12b",
    },
}

ALL_MODELS = NVIDIA_MODELS
DEFAULT_MODEL = "nvidia:phi-4-mini-instruct"

# Tools that warrant using the coding-specialist model
CODING_TOOLS = {"FileEditTool", "BashTool"}
FAST_TOOLS = {"ListDirTool", "FileReadTool", "ViewFileLinesTool"}


def get_all_models() -> dict[str, dict]:
    return NVIDIA_MODELS


class LLMClient:
    """LLM client backed by NVIDIA's API (OpenAI-compatible)."""

    def __init__(self, model: str = None):
        self.model = model or os.environ.get("CLAW_MODEL", DEFAULT_MODEL)

    def is_smart(self) -> bool:
        return False  # no smart routing needed — all models are on same provider

    def _count_tokens(self, text: str) -> int:
        # Estimate ~2 chars per token for code/mixed text to be safe
        return max(1, len(text) // 2)

    def _get_safe_max_tokens(self, messages: list, ctx_limit: int, desired: int = 4096) -> int:
        total_input = sum(self._count_tokens(m.get("content", "")) for m in messages)
        remaining = ctx_limit - total_input - 300  # 300-token safety buffer
        return min(desired, max(512, remaining))

    def _trim_messages(self, messages: list, ctx_limit: int, completion_budget: int = 4096) -> list:
        max_input = ctx_limit - completion_budget - 500  # 500-token safety buffer
        system_msgs = [m for m in messages if m.get("role") == "system"]
        other_msgs  = [m for m in messages if m.get("role") != "system"]

        while True:
            total = sum(self._count_tokens(m.get("content", "")) for m in system_msgs + other_msgs)
            if total <= max_input or len(other_msgs) <= 1:
                break
            other_msgs.pop(0)

        # Truncate if still too large
        total = sum(self._count_tokens(m.get("content", "")) for m in system_msgs + other_msgs)
        if total > max_input and other_msgs:
            m = other_msgs[0]
            sys_toks = sum(self._count_tokens(m.get("content", "")) for m in system_msgs)
            target = max_input - sys_toks
            if target > 100:
                content = m.get("content", "")
                char_lim = target * 2
                m["content"] = content[:char_lim] + "... [TRUNCATED]"
        return system_msgs + other_msgs

    def chat(self, messages: List[Dict[str, str]], turn_type: str = "default") -> str:
        api_key = get_nvidia_key()
        if not api_key:
            return (
                "CLAW_ERROR:NO_KEY:nvidia|"
                "No NVIDIA API key configured. Add your key in the ⚙ Settings panel."
            )

        info = NVIDIA_MODELS.get(self.model, NVIDIA_MODELS[DEFAULT_MODEL])
        nvidia_model_id = info.get("nvidia_id", "microsoft/phi-4-mini-instruct")
        ctx_limit = info.get("context", 32768)

        # Enforce context limits
        messages = self._trim_messages(messages, ctx_limit, 4096)
        max_tok = self._get_safe_max_tokens(messages, ctx_limit, 4096)

        try:
            client = OpenAI(
                base_url=NVIDIA_BASE_URL,
                api_key=api_key,
            )
            temperature = info.get("temperature", 0.2)
            top_p = info.get("top_p", 0.7)

            completion = client.chat.completions.create(
                model=nvidia_model_id,
                messages=messages,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tok,
                stream=True,
            )
            result = ""
            for chunk in completion:
                if not getattr(chunk, "choices", None):
                    continue
                delta = chunk.choices[0].delta
                if delta.content is not None:
                    result += delta.content

            # Strip DeepSeek-style <think> reasoning blocks
            result = _strip_think_tags(result)
            return result

        except Exception as e:
            err = str(e)
            if "401" in err or "unauthorized" in err.lower():
                return (
                    "CLAW_ERROR:BAD_KEY:nvidia|"
                    "Invalid NVIDIA API key (401). Update it in ⚙ Settings."
                )
            if "429" in err or "rate" in err.lower():
                return "CLAW_ERROR:RATE_LIMIT:nvidia|NVIDIA rate limit hit. Try again in a moment."
            if "404" in err:
                return (
                    f"CLAW_ERROR:API_ERROR:nvidia|"
                    f"Model not found (404). The model '{nvidia_model_id}' may be unavailable."
                )
            return f"NVIDIA API error: {err}"

    def chat_stream(self, messages: List[Dict[str, str]], turn_type: str = "default"):
        api_key = get_nvidia_key()
        if not api_key:
            yield "CLAW_ERROR:NO_KEY:nvidia|No NVIDIA API key configured. Add your key in the ⚙ Settings panel."
            return

        info = NVIDIA_MODELS.get(self.model, NVIDIA_MODELS[DEFAULT_MODEL])
        nvidia_model_id = info.get("nvidia_id", "microsoft/phi-4-mini-instruct")
        ctx_limit = info.get("context", 32768)

        # Enforce context limits
        messages = self._trim_messages(messages, ctx_limit, 4096)
        max_tok = self._get_safe_max_tokens(messages, ctx_limit, 4096)

        try:
            client = OpenAI(
                base_url=NVIDIA_BASE_URL,
                api_key=api_key,
            )
            temperature = info.get("temperature", 0.2)
            top_p = info.get("top_p", 0.7)

            completion = client.chat.completions.create(
                model=nvidia_model_id,
                messages=messages,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tok,
                stream=True,
            )
            for chunk in completion:
                if not getattr(chunk, "choices", None):
                    continue
                delta = chunk.choices[0].delta
                if delta.content is not None:
                    yield delta.content

        except Exception as e:
            err = str(e)
            if "401" in err or "unauthorized" in err.lower():
                yield "CLAW_ERROR:BAD_KEY:nvidia|Invalid NVIDIA API key (401). Update it in ⚙ Settings."
            elif "429" in err or "rate" in err.lower():
                yield "CLAW_ERROR:RATE_LIMIT:nvidia|NVIDIA rate limit hit. Try again in a moment."
            elif "404" in err:
                yield f"CLAW_ERROR:API_ERROR:nvidia|Model not found (404). The model '{nvidia_model_id}' may be unavailable."
            else:
                yield f"NVIDIA API error: {err}"

    def get_active_model_info(self) -> dict:
        return NVIDIA_MODELS.get(self.model, {})

    # Keep these for compatibility with agent.py smart routing checks
    def route(self, turn_type: str) -> str:
        return self.model

    def route_for_tool(self, tool_name: str | None) -> str:
        return self.model


# ── Helpers ──────────────────────────────────────────────────────────────────

def _strip_think_tags(text: str) -> str:
    return re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()


def validate_key(key: str) -> dict:
    """Test an NVIDIA API key by listing available models."""
    try:
        client = OpenAI(base_url=NVIDIA_BASE_URL, api_key=key)
        models = client.models.list()
        count = len(list(models))
        return {"ok": True, "message": f"NVIDIA key valid ✓ — {count} models available"}
    except Exception as e:
        err = str(e)
        if "401" in err or "unauthorized" in err.lower():
            return {"ok": False, "message": "Invalid key (401 Unauthorized). Check your NVIDIA API key."}
        return {"ok": False, "message": f"Connection error: {err[:120]}"}


# Backwards-compatible aliases
OpenRouterClient = LLMClient
SMART_MODELS = {}

def set_runtime_key_compat(provider: str, key: str):
    """Backwards-compatible key setter — provider arg is ignored (always nvidia)."""
    set_runtime_key(key)

def refresh_all_models():
    return NVIDIA_MODELS, None

def get_key(provider: str, env_var: str) -> str:
    return get_nvidia_key()

def _or_model_cache():
    return {"fetched_at": 0, "error": None}
