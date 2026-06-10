"""Token usage and cost tracking."""

from __future__ import annotations

from dataclasses import dataclass, field

# Pricing per million tokens (USD), mirroring cc's canonical model cost tiers
# (cc/src/utils/modelCost.ts). Each tuple is:
#   (input_per_M, output_per_M, cache_write_per_M, cache_read_per_M)
#
# cc tiers:
#   COST_TIER_15_75 (Opus 4 / 4.1)   = 15 / 75   / 18.75 / 1.5
#   COST_TIER_5_25  (Opus 4.5 / 4.6) =  5 / 25   /  6.25 / 0.5
#   COST_TIER_3_15  (Sonnet 3.5v2/3.7/4/4.5/4.6) = 3 / 15 / 3.75 / 0.3
#   COST_HAIKU_45   (Haiku 4.5)      =  1 /  5   /  1.25 / 0.1
#   COST_HAIKU_35   (Haiku 3.5)      =  0.8 / 4  /  1.0  / 0.08
#   COST_TIER_30_150 (Opus 4.6 fast) = 30 / 150  / 37.5  / 3
_COST_TIER_15_75 = (15.0, 75.0, 18.75, 1.5)
_COST_TIER_5_25 = (5.0, 25.0, 6.25, 0.5)
_COST_TIER_3_15 = (3.0, 15.0, 3.75, 0.3)
_COST_TIER_30_150 = (30.0, 150.0, 37.5, 3.0)
_COST_HAIKU_45 = (1.0, 5.0, 1.25, 0.1)
_COST_HAIKU_35 = (0.8, 4.0, 1.0, 0.08)

# cc falls back to the default main-loop model's tier, which is effectively
# COST_TIER_5_25 (DEFAULT_UNKNOWN_MODEL_COST) for unknown models.
_DEFAULT_UNKNOWN_MODEL_COST = _COST_TIER_5_25

# (input_per_M, output_per_M, cache_write_per_M, cache_read_per_M)
_PRICING: dict[str, tuple[float, float, float, float]] = {
    # ── Anthropic ──────────────────────────────────────────────
    # Opus 4 / 4.1 → COST_TIER_15_75
    "claude-opus-4-20250514": _COST_TIER_15_75,
    "claude-opus-4-1": _COST_TIER_15_75,
    "claude-opus-4-1-20250805": _COST_TIER_15_75,
    # Opus 4.5 / 4.6 → COST_TIER_5_25 (NOT 15/75)
    "claude-opus-4-5": _COST_TIER_5_25,
    "claude-opus-4-6": _COST_TIER_5_25,
    # Sonnet family (3.5v2 / 3.7 / 4 / 4.5 / 4.6) → COST_TIER_3_15
    "claude-3-5-sonnet-20241022": _COST_TIER_3_15,
    "claude-3-7-sonnet": _COST_TIER_3_15,
    "claude-sonnet-4-20250514": _COST_TIER_3_15,
    "claude-sonnet-4-5": _COST_TIER_3_15,
    "claude-sonnet-4-6": _COST_TIER_3_15,
    # Haiku 4.5 → COST_HAIKU_45
    "claude-haiku-4-5-20251001": _COST_HAIKU_45,
    # Haiku 3.5 → COST_HAIKU_35
    "claude-3-5-haiku-20241022": _COST_HAIKU_35,
    # ── OpenAI ─────────────────────────────────────────────────
    # (input, output, cache_write, cache_read) — OpenAI bills cached input at
    # the read rate only; no separate cache-write charge, so write == input.
    "gpt-4o": (2.50, 10.0, 2.50, 1.25),
    "gpt-4o-mini": (0.15, 0.60, 0.15, 0.075),
    "gpt-4.1": (2.0, 8.0, 2.0, 0.50),
    "gpt-4.1-mini": (0.40, 1.60, 0.40, 0.10),
    "gpt-4.1-nano": (0.10, 0.40, 0.10, 0.025),
    "o1": (15.0, 60.0, 15.0, 7.50),
    "o1-mini": (1.10, 4.40, 1.10, 0.55),
    "o3": (10.0, 40.0, 10.0, 2.50),
    "o3-mini": (1.10, 4.40, 1.10, 0.55),
    "o4-mini": (1.10, 4.40, 1.10, 0.275),
    # ── xAI / Grok ─────────────────────────────────────────────
    "grok-3": (3.0, 15.0, 3.0, 0.75),
    "grok-3-mini": (0.30, 0.50, 0.30, 0.075),
    # ── Ollama (local, free) ───────────────────────────────────
    "llama3.1": (0.0, 0.0, 0.0, 0.0),
    "llama3.2": (0.0, 0.0, 0.0, 0.0),
    "llama3.3": (0.0, 0.0, 0.0, 0.0),
    "codellama": (0.0, 0.0, 0.0, 0.0),
    "deepseek-coder": (0.0, 0.0, 0.0, 0.0),
    "qwen2.5-coder": (0.0, 0.0, 0.0, 0.0),
    "mistral": (0.0, 0.0, 0.0, 0.0),
}


def _resolve_pricing(model: str) -> tuple[float, float, float, float]:
    """Resolve a model id to a pricing tuple.

    Matches on an exact id first, then on a canonical short-name substring so
    dated aliases (e.g. ``claude-opus-4-6-20260115``) resolve to the right tier.
    Falls back to cc's default-unknown-model tier (COST_TIER_5_25).
    """
    exact = _PRICING.get(model)
    if exact is not None:
        return exact

    name = model.lower()
    # Canonical short-name matching, most specific first.
    if "opus-4-6" in name or "opus-4.6" in name:
        return _COST_TIER_5_25
    if "opus-4-5" in name or "opus-4.5" in name:
        return _COST_TIER_5_25
    if "opus-4" in name or "opus4" in name:
        return _COST_TIER_15_75
    if "sonnet" in name:
        return _COST_TIER_3_15
    if "haiku-4" in name or "haiku4" in name:
        return _COST_HAIKU_45
    if "haiku" in name:
        return _COST_HAIKU_35
    # Unknown model → cc's DEFAULT_UNKNOWN_MODEL_COST (COST_TIER_5_25).
    return _DEFAULT_UNKNOWN_MODEL_COST


@dataclass
class CostTracker:
    """Track token usage and estimated cost across a session."""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0
    turn_count: int = 0
    # Per-model: (input, output, cache_read, cache_creation)
    _model_tokens: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)

    def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> None:
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cache_read_tokens += cache_read_tokens
        self.total_cache_creation_tokens += cache_creation_tokens
        self.turn_count += 1

        prev = self._model_tokens.get(model, (0, 0, 0, 0))
        self._model_tokens[model] = (
            prev[0] + input_tokens,
            prev[1] + output_tokens,
            prev[2] + cache_read_tokens,
            prev[3] + cache_creation_tokens,
        )

    def estimate_cost(self) -> float:
        """Estimate total cost in USD, billing cache reads and writes too.

        Mirrors cc's tokensToUSDCost(): input + output + cache_read + cache_write.
        """
        total = 0.0
        for model, (inp, out, cache_read, cache_creation) in self._model_tokens.items():
            input_per_m, output_per_m, cache_write_per_m, cache_read_per_m = _resolve_pricing(model)
            total += (inp / 1_000_000) * input_per_m
            total += (out / 1_000_000) * output_per_m
            total += (cache_read / 1_000_000) * cache_read_per_m
            total += (cache_creation / 1_000_000) * cache_write_per_m
        return total

    def summary(self) -> str:
        cost = self.estimate_cost()
        return (
            f"Tokens: {self.total_input_tokens:,} in / {self.total_output_tokens:,} out | "
            f"Turns: {self.turn_count} | "
            f"Cost: ${cost:.4f}"
        )
