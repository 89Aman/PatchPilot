"""Official Free Models Catalog for PatchPilot BYOK Vault.

Maintains curated, verified free-tier models and endpoints across official LLM providers.
"""

from typing import Any

OFFICIAL_FREE_MODELS: dict[str, dict[str, Any]] = {
    "gemini": {
        "provider_name": "Google Gemini",
        "badge": "Free Tier Available",
        "free_tier_info": "Google AI Studio: 15 RPM, 1M TPM, 1,500 RPD (Free, no credit card required)",
        "get_key_url": "https://aistudio.google.com/app/apikey",
        "models": [
            {
                "id": "gemini-2.5-flash",
                "name": "Gemini 2.5 Flash",
                "tag": "Recommended",
                "speed": "Ultra Fast (~250 tok/s)",
                "context": "1,048,576 tokens",
                "description": "State-of-the-art fast multimodal model with huge 1M context window.",
            },
            {
                "id": "gemini-2.5-pro",
                "name": "Gemini 2.5 Pro",
                "tag": "Deep Reasoning",
                "speed": "Fast (~100 tok/s)",
                "context": "2,097,152 tokens",
                "description": "Flagship reasoning and code architecture analysis model with 2M context.",
            },
            {
                "id": "gemini-2.0-flash",
                "name": "Gemini 2.0 Flash",
                "tag": "Standard",
                "speed": "Ultra Fast (~300 tok/s)",
                "context": "1,048,576 tokens",
                "description": "High-throughput general purpose code inspection model.",
            },
        ],
    },
    "groq": {
        "provider_name": "Groq Cloud",
        "badge": "Free Developer Tier",
        "free_tier_info": "Groq Console: 30 RPM, 14,400 RPD, 300+ tok/s LPUs (Free developer quota)",
        "get_key_url": "https://console.groq.com/keys",
        "models": [
            {
                "id": "llama-3.3-70b-versatile",
                "name": "Llama 3.3 70B Versatile",
                "tag": "Top Quality",
                "speed": "280 tok/s",
                "context": "128,000 tokens",
                "description": "Flagship open weights model optimized on Groq LPU hardware.",
            },
            {
                "id": "llama-3.1-8b-instant",
                "name": "Llama 3.1 8B Instant",
                "tag": "Fastest",
                "speed": "500+ tok/s",
                "context": "128,000 tokens",
                "description": "Lightning-fast code reviews and low-latency diff analysis.",
            },
            {
                "id": "qwen-2.5-coder-32b",
                "name": "Qwen 2.5 Coder 32B",
                "tag": "Coding Specialist",
                "speed": "350 tok/s",
                "context": "128,000 tokens",
                "description": "Specialized coding LLM fine-tuned for syntax, bugs, and tests.",
            },
        ],
    },
    "openrouter": {
        "provider_name": "OpenRouter (:free)",
        "badge": "100% Free (:free)",
        "free_tier_info": "OpenRouter Free Models: 0 cost, requires 0 balance or credit card",
        "get_key_url": "https://openrouter.ai/keys",
        "models": [
            {
                "id": "deepseek/deepseek-r1:free",
                "name": "DeepSeek R1 (Free)",
                "tag": "Reasoning",
                "speed": "Medium (~50 tok/s)",
                "context": "64,000 tokens",
                "description": "Advanced reasoning and deep logical bug verification.",
            },
            {
                "id": "meta-llama/llama-3.3-70b-instruct:free",
                "name": "Llama 3.3 70B Instruct (Free)",
                "tag": "General",
                "speed": "Fast (~120 tok/s)",
                "context": "128,000 tokens",
                "description": "Free open weights model hosted on community inference pools.",
            },
            {
                "id": "google/gemini-2.0-flash-exp:free",
                "name": "Gemini 2.0 Flash Exp (Free)",
                "tag": "Experimental",
                "speed": "Fast (~180 tok/s)",
                "context": "1,048,576 tokens",
                "description": "Google experimental release routed through OpenRouter free tier.",
            },
            {
                "id": "qwen/qwen-2.5-coder-32b-instruct:free",
                "name": "Qwen 2.5 Coder 32B (Free)",
                "tag": "Coding",
                "speed": "Fast (~150 tok/s)",
                "context": "32,768 tokens",
                "description": "Open code generation model with zero credit consumption.",
            },
        ],
    },
    "ollama": {
        "provider_name": "Ollama (Self-Hosted)",
        "badge": "100% Free & Local",
        "free_tier_info": "Local / Tunneled: Unlimited runs on your own hardware or cloud VM",
        "get_key_url": "https://ollama.com",
        "models": [
            {
                "id": "qwen2.5-coder:14b",
                "name": "Qwen 2.5 Coder 14B",
                "tag": "Local Coding",
                "speed": "Hardware Dependent",
                "context": "32,768 tokens",
                "description": "Excellent performance on local workstations for private code reviews.",
            },
            {
                "id": "llama3.1:8b",
                "name": "Llama 3.1 8B",
                "tag": "Local General",
                "speed": "Hardware Dependent",
                "context": "8,192 tokens",
                "description": "Lightweight model suitable for CPU or low VRAM setups.",
            },
        ],
    },
}


def get_free_models_catalog() -> list[dict[str, Any]]:
    """Return flattened list of all verified free models across providers."""
    items: list[dict[str, Any]] = []
    for provider_key, data in OFFICIAL_FREE_MODELS.items():
        for m in data.get("models", []):
            items.append({
                "id": m["id"],
                "provider": provider_key,
                "provider_name": data.get("provider_name"),
                "name": m["name"],
                "quota_summary": data.get("free_tier_info", ""),
                "key_url": data.get("get_key_url", ""),
                "is_free": True,
                "tag": m.get("tag", ""),
                "speed": m.get("speed", ""),
                "context": m.get("context", ""),
                "description": m.get("description", ""),
            })
    return items
