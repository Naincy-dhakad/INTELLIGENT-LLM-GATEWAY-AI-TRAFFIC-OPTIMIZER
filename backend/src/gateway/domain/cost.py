"""Provider-independent bounded cost estimation for routing."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from typing import Sequence


TOKENS_PER_MILLION = Decimal("1000000")
# A conservative bounded estimate used only when the caller omits max output tokens.
DEFAULT_OUTPUT_TOKENS = 256
MAX_ESTIMATED_OUTPUT_TOKENS = 4096


@dataclass(frozen=True)
class CostMessage:
    content: str


@dataclass(frozen=True)
class TokenEstimate:
    input_tokens: int
    output_tokens: int


def estimate_token_counts(
    messages: Sequence[CostMessage], max_output_tokens: int | None = None
) -> TokenEstimate:
    """Approximate tokens from UTF-8-independent character count, without I/O.

    Four characters are treated as one token, rounded up. Output is bounded to
    4096 tokens when the request does not provide a smaller explicit maximum.
    This is a routing estimate, not provider billing or tokenizer output.
    """

    input_characters = sum(len(message.content) for message in messages)
    input_tokens = max(1, (input_characters + 3) // 4)
    output_tokens = min(
        max_output_tokens if max_output_tokens is not None else DEFAULT_OUTPUT_TOKENS,
        MAX_ESTIMATED_OUTPUT_TOKENS,
    )
    return TokenEstimate(input_tokens=input_tokens, output_tokens=max(1, output_tokens))


def estimated_cost_usd(
    tokens: TokenEstimate,
    input_usd_per_million_tokens: Decimal,
    output_usd_per_million_tokens: Decimal,
) -> Decimal:
    """Calculate a normalized USD estimate using USD per million tokens."""

    value = (
        Decimal(tokens.input_tokens) * input_usd_per_million_tokens
        + Decimal(tokens.output_tokens) * output_usd_per_million_tokens
    ) / TOKENS_PER_MILLION
    return value.quantize(Decimal("0.00000001"), rounding=ROUND_CEILING)
