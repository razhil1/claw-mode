import os
import json
import urllib.request
import urllib.error
from typing import List, Dict

# ===================== RUNTIME KEY STORE (in-memory override) =====================
_runtime_keys: dict[str, str] = {}

def set_runtime_key(provider: str, key: str):
    _runtime_keys[provider] = key

def get_key(provider: str, env_var: str) -> str:
    return _runtime_keys.get(provider) or os.environ.get(env_var, "")

# ===================== OPENROUTER MODELS =====================
OPENROUTER_MODELS = {
    "deepseek/deepseek-chat-v3-0324:free": {
        "label": "DeepSeek V3 (Free)",
        "description": "Fast, highly capable coding model. Great for full-stack generation.",
        "context": 131072, "tier": "free", "provider": "openrouter",
    },
    "deepseek/deepseek-r1:free": {
        "label": "DeepSeek R1 — Reasoning (Free)",
        "description": "Best free reasoning model. Shows step-by-step thinking.",
        "context": 163840, "tier": "free", "provider": "openrouter",
    },
    "qwen/qwen-2.5-coder-32b-instruct:free": {
        "label": "Qwen 2.5 Coder 32B (Free)",
        "description": "State-of-the-art open-source coding model by Alibaba.",
        "context": 32768, "tier": "free", "provider": "openrouter",
    },
    "google/gemini-2.0-flash-thinking-exp:free": {
        "label": "Gemini 2.0 Flash Thinking (Free)",
        "description": "Google's fast reasoning model with visible thought process.",
        "context": 32767, "tier": "free", "provider": "openrouter",
    },
    "google/gemini-2.5-pro-exp-03-25:free": {
        "label": "Gemini 2.5 Pro Exp (Free)",
        "description": "Google's latest frontier model. Incredible 1M context window.",
        "context": 1000000, "tier": "free", "provider": "openrouter",
    },
    "meta-llama/llama-4-maverick:free": {
        "label": "Llama 4 Maverick (Free)",
        "description": "Meta's newest model. Strong coding and reasoning capabilities.",
        "context": 524288, "tier": "free", "provider": "openrouter",
    },
    "microsoft/phi-4:free": {
        "label": "Microsoft Phi-4 (Free)",
        "description": "Microsoft's compact but powerful model. Excellent for code.",
        "context": 16384, "tier": "free", "provider": "openrouter",
    },
    "anthropic/claude-3.5-sonnet": {
        "label": "Claude 3.5 Sonnet ★",
        "description": "Gold standard for coding. Best for architecture & multi-file edits.",
        "context": 200000, "tier": "paid", "provider": "openrouter",
    },
    "anthropic/claude-3.5-haiku": {
        "label": "Claude 3.5 Haiku ★",
        "description": "Fast and affordable Claude. Great for iterative coding tasks.",
        "context": 200000, "tier": "paid", "provider": "openrouter",
    },
    "openai/gpt-4o": {
        "label": "GPT-4o ★",
        "description": "OpenAI's flagship model. Excellent reasoning and code.",
        "context": 128000, "tier": "paid", "provider": "openrouter",
    },
    "openai/gpt-4o-mini": {
        "label": "GPT-4o Mini ★",
        "description": "Fast, affordable GPT-4 class model. Great for quick tasks.",
        "context": 128000, "tier": "paid", "provider": "openrouter",
    },
}

# ===================== GROQ MODELS =====================
GROQ_MODELS = {
    "groq:llama-3.3-70b-versatile": {
        "label": "Llama 3.3 70B (Groq Free)",
        "description": "Meta's best open model on ultra-fast Groq inference. Excellent for code.",
        "context": 128000, "tier": "free", "provider": "groq",
        "groq_id": "llama-3.3-70b-versatile",
    },
    "groq:llama-3.1-8b-instant": {
        "label": "Llama 3.1 8B Instant (Groq Free)",
        "description": "Lightning-fast small model. Great for quick tasks and iterations.",
        "context": 128000, "tier": "free", "provider": "groq",
        "groq_id": "llama-3.1-8b-instant",
    },
    "groq:gemma2-9b-it": {
        "label": "Gemma 2 9B (Groq Free)",
        "description": "Google's Gemma 2 on Groq. Fast and capable for coding.",
        "context": 8192, "tier": "free", "provider": "groq",
        "groq_id": "gemma2-9b-it",
    },
    "groq:mixtral-8x7b-32768": {
        "label": "Mixtral 8x7B (Groq Free)",
        "description": "Mistral's MoE model. Strong reasoning and instruction following.",
        "context": 32768, "tier": "free", "provider": "groq",
        "groq_id": "mixtral-8x7b-32768",
    },
    "groq:deepseek-r1-distill-llama-70b": {
        "label": "DeepSeek R1 Distill 70B (Groq Free)",
        "description": "DeepSeek's reasoning model distilled into Llama 70B. Very fast on Groq.",
        "context": 128000, "tier": "free", "provider": "groq",
        "groq_id": "deepseek-r1-distill-llama-70b",
    },
    "groq:llama-3.3-70b-specdec": {
        "label": "Llama 3.3 70B SpecDec (Groq Free)",
        "description": "Llama 3.3 70B with speculative decoding — even faster token generation.",
        "context": 8192, "tier": "free", "provider": "groq",
        "groq_id": "llama-3.3-70b-specdec",
    },
}

CODING_MODELS = {**GROQ_MODELS, **OPENROUTER_MODELS}
DEFAULT_MODEL = "groq:llama-3.3-70b-versatile"


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
    """Unified LLM client supporting OpenRouter and Groq."""

    def __init__(self, model: str = None):
        self.model = model or os.environ.get("CLAW_MODEL", DEFAULT_MODEL)

    def _get_provider(self) -> str:
        info = CODING_MODELS.get(self.model, {})
        return info.get("provider", "openrouter")

    def _get_groq_model_id(self) -> str:
        info = CODING_MODELS.get(self.model, {})
        return info.get("groq_id", self.model.replace("groq:", ""))

    def chat(self, messages: List[Dict[str, str]]) -> str:
        provider = self._get_provider()
        try:
            if provider == "groq":
                return self._call_groq(messages)
            else:
                return self._call_openrouter(messages)
        except Exception as e:
            return f"Error: {str(e)}"

    def _call_groq(self, messages: List[Dict[str, str]]) -> str:
        api_key = get_key("groq", "GROQ_API_KEY")
        if not api_key:
            return (
                "CLAW_ERROR:NO_KEY:groq|"
                "No Groq API key found. Get a free key at https://console.groq.com "
                "and add it in the ⚙ Settings panel."
            )

        body = {
            "model": self._get_groq_model_id(),
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
                return result["choices"][0]["message"]["content"]
            return f"Groq API Error: {json.dumps(result)}"
        except urllib.error.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode("utf-8")
            except Exception:
                pass
            if e.code == 401:
                return (
                    "CLAW_ERROR:BAD_KEY:groq|"
                    f"Invalid Groq API key (401). Please update it in ⚙ Settings. Detail: {body_text[:200]}"
                )
            if e.code == 429:
                return "CLAW_ERROR:RATE_LIMIT:groq|Groq rate limit hit. Try a different model or wait a moment."
            if e.code == 503:
                return "CLAW_ERROR:SERVER_ERROR:groq|Groq service temporarily unavailable. Try again in a few seconds."
            return f"Groq HTTP {e.code}: {body_text[:300]}"
        except Exception as e:
            return f"Groq connection error: {str(e)}"

    def _call_openrouter(self, messages: List[Dict[str, str]]) -> str:
        api_key = get_key("openrouter", "OPENROUTER_API_KEY")
        if not api_key:
            return (
                "CLAW_ERROR:NO_KEY:openrouter|"
                "No OpenRouter API key found. Get a free key at https://openrouter.ai/keys "
                "and add it in the ⚙ Settings panel."
            )

        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 8000,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://github.com/instructkr/claw-code",
            "X-Title": "Claw IDE",
            "Content-Type": "application/json",
        }
        try:
            result = _post_json("https://openrouter.ai/api/v1/chat/completions", headers, body)
            if "choices" in result:
                return result["choices"][0]["message"]["content"]
            return f"OpenRouter API Error: {json.dumps(result)}"
        except urllib.error.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode("utf-8")
            except Exception:
                pass
            if e.code == 401:
                return (
                    "CLAW_ERROR:BAD_KEY:openrouter|"
                    f"Invalid OpenRouter API key (401). "
                    "Please create a fresh key at https://openrouter.ai/keys and add it in ⚙ Settings."
                )
            if e.code == 402:
                return "CLAW_ERROR:NO_CREDITS:openrouter|Insufficient credits. Add credits at https://openrouter.ai or switch to a free model."
            if e.code == 429:
                return "CLAW_ERROR:RATE_LIMIT:openrouter|OpenRouter rate limit hit. Try switching to a different model."
            if e.code == 503:
                return "CLAW_ERROR:SERVER_ERROR:openrouter|OpenRouter service temporarily unavailable. Try again shortly."
            return f"OpenRouter HTTP {e.code}: {body_text[:300]}"
        except Exception as e:
            return f"OpenRouter connection error: {str(e)}"


def validate_key(provider: str, key: str) -> dict:
    """Test an API key. Returns {ok, message}."""
    try:
        if provider == "groq":
            req = urllib.request.Request(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {key}"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
                count = len(data.get("data", []))
                return {"ok": True, "message": f"Groq key valid — {count} models available"}
        else:
            body = json.dumps({
                "model": "openrouter/auto",
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 1,
            }).encode()
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=body,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/instructkr/claw-code",
                    "X-Title": "Claw IDE",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                return {"ok": True, "message": "OpenRouter key valid ✓"}
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8")
        except Exception:
            pass
        if e.code == 401:
            return {"ok": False, "message": f"Invalid key — account not found (401). {body_text[:120]}"}
        if e.code == 402:
            return {"ok": True, "message": "Key valid (insufficient credits for paid models — free models still work)"}
        return {"ok": False, "message": f"HTTP {e.code}: {body_text[:120]}"}
    except Exception as ex:
        return {"ok": False, "message": f"Connection error: {str(ex)}"}


# Backwards-compatible alias
OpenRouterClient = LLMClient
