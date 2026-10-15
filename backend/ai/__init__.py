"""AI layer: OpenAI-compatible LLM access shared by understanding and NL Patch.

The package deliberately contains no Z-Sheet domain logic — it only speaks the
OpenAI ``/chat/completions`` dialect so any compatible service (DeepSeek,
Qwen/DashScope, OpenAI gateways) or local engine (Ollama, vLLM) can be used by
pointing ``ZSHEET_LLM_BASE_URL`` / ``ZSHEET_LLM_MODEL`` at it.
"""
