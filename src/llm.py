import os
import json
import urllib.request
import urllib.error
from typing import List, Dict

# ===================== BEST CODING MODELS ON OPENROUTER =====================
# Only including 100% VERIFIED and stable model IDs (April 2026)
CODING_MODELS = {
    # === FREE TIER — Best for coding ===
    "deepseek/deepseek-r1:free": {
        "label": "DeepSeek R1 (Free)",
        "description": "Best free reasoning+coding model. Strong logical thinking & code gen.",
        "context": 163840,
        "tier": "free"
    },
    "deepseek/deepseek-chat-v3-0324:free": {
        "label": "DeepSeek V3 (Free)",
        "description": "Fast, highly capable coding model. Great for full-stack generation.",
        "context": 131072,
        "tier": "free"
    },
    "qwen/qwen-2.5-coder-32b-instruct:free": {
        "label": "Qwen 2.5 Coder 32B (Free)",
        "description": "SOTA open-source coding model. Excellent tool use and precision.",
        "context": 32768,
        "tier": "free"
    },
    "google/gemini-2.0-flash-thinking-exp:free": {
        "label": "Gemini 2.0 Flash Thinking (Free)",
        "description": "Fast reasoning + coding. Shows its thinking process.",
        "context": 32767,
        "tier": "free"
    },
    "openrouter/free": {
        "label": "OpenRouter Auto-Free",
        "description": "Automatically picks the best available free model right now.",
        "context": 32768,
        "tier": "free"
    },

    # === PAID — For maximum quality ===
    "anthropic/claude-3.5-sonnet": {
        "label": "Claude 3.5 Sonnet ★",
        "description": "Gold standard for coding. Best for architecture & multi-file edits.",
        "context": 200000,
        "tier": "paid"
    },
    "anthropic/claude-3-5-haiku-20241022": {
        "label": "Claude 3.5 Haiku",
        "description": "Ultra-fast and very smart at coding for the price.",
        "context": 200000,
        "tier": "paid"
    },
    "google/gemini-2.0-pro-exp-02-05:free": {
        "label": "Gemini 2.0 Pro Exp (Free)",
        "description": "Google's experimental high-reasoning model. Huge 1M context.",
        "context": 1000000,
        "tier": "free"
    },
    "openai/gpt-4o": {
        "label": "GPT-4o",
        "description": "OpenAI's flagship coding model.",
        "context": 128000,
        "tier": "paid"
    },
}

# Default model — verified stable
DEFAULT_MODEL = "deepseek/deepseek-chat-v3-0324:free"

class OpenRouterClient:
    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self.model = model or os.environ.get("CLAW_MODEL", DEFAULT_MODEL)
        self.base_url = "https://openrouter.ai/api/v1/chat/completions"

    def chat(self, messages: List[Dict[str, str]]) -> str:
        if not self.api_key:
            return "Error: OPENROUTER_API_KEY not found. Please set it in your environment."

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/instructkr/claw-code",
            "X-Title": "Claw IDE",
            "Content-Type": "application/json"
        }

        data = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,      # Even lower for more reliable code
            "max_tokens": 8000,       
        }

        req = urllib.request.Request(
            self.base_url,
            data=json.dumps(data).encode("utf-8"),
            headers=headers,
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                result = json.loads(response.read().decode("utf-8"))
                if "choices" in result:
                    return result["choices"][0]["message"]["content"]
                else:
                    return f"API Error: {json.dumps(result)}"
        except urllib.error.HTTPError as e:
            try:
                error_body = e.read().decode("utf-8")
                return f"HTTP Error {e.code}: {error_body}"
            except:
                return f"HTTP Error {e.code}"
        except Exception as e:
            return f"Error calling OpenRouter: {str(e)}"
