"""LLM providers (OpenAI, OpenRouter), the model allowlist, usage and budgets (WP4b).

registry.py holds the providers and models, gateway.py is the one path admin-built capabilities use to
call a model (allowlist, RBAC, data class, budget), usage.py records tokens and cost.
"""
