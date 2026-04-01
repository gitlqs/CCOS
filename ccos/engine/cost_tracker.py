"""Token usage and cost tracking."""

from __future__ import annotations

from dataclasses import dataclass, field

# Pricing per million tokens (USD) — approximate, as of 2025-2026
_PRICING: dict[str, tuple[float, float]] = {
    # (input_per_M, output_per_M)
    # Anthropic
    "claude-opus-4-6": (15.0, 75.0),
    "claude-opus-4-20250514": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-20250514": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
    "claude-3-5-sonnet-20241022": (3.0, 15.0),
    "claude-3-5-haiku-20241022": (0.80, 4.0),
    # OpenAI
    "gpt-4o": (2.50, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "o1": (15.0, 60.0),
    "o1-mini": (1.10, 4.40),
    "o3": (10.0, 40.0),
    "o3-mini": (1.10, 4.40),
    "o4-mini": (1.10, 4.40),
    # xAI / Grok
    "grok-3": (3.0, 15.0),
    "grok-3-mini": (0.30, 0.50),
    # Ollama (local, free)
    "llama3.1": (0.0, 0.0),
    "llama3.2": (0.0, 0.0),
    "llama3.3": (0.0, 0.0),
    "codellama": (0.0, 0.0),
    "deepseek-coder": (0.0, 0.0),
    "qwen2.5-coder": (0.0, 0.0),
    "mistral": (0.0, 0.0),
}


@dataclass
class CostTracker:
    """Track token usage and estimated cost across a session."""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0
    turn_count: int = 0
    _model_tokens: dict[str, tuple[int, int]] = field(default_factory=dict)

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

        prev = self._model_tokens.get(model, (0, 0))
        self._model_tokens[model] = (
            prev[0] + input_tokens,
            prev[1] + output_tokens,
        )

    def estimate_cost(self) -> float:
        """Estimate total cost in USD."""
        total = 0.0
        for model, (inp, out) in self._model_tokens.items():
            pricing = _PRICING.get(model)
            if pricing:
                total += (inp / 1_000_000) * pricing[0]
                total += (out / 1_000_000) * pricing[1]
            else:
                # Fallback: rough estimate
                total += (inp / 1_000_000) * 3.0
                total += (out / 1_000_000) * 15.0
        return total

    def summary(self) -> str:
        cost = self.estimate_cost()
        return (
            f"Tokens: {self.total_input_tokens:,} in / {self.total_output_tokens:,} out | "
            f"Turns: {self.turn_count} | "
            f"Cost: ${cost:.4f}"
        )
