"""Price Live's delegated usage, including explicit cache-write tokens."""

from jarvis.brain.cost import calculate_cost_usd


def backend_cost_usd(model: str, usage: dict) -> float:
    """Input totals include both cache reads and writes; charge each exactly once.

    Cache writes reported by Responses cost 1.25 times the standard input rate.
    Older models without this usage field keep their existing input accounting.
    Source: https://developers.openai.com/api/docs/guides/prompt-caching
    """
    total = max(0, int(usage.get("input_tokens", 0)))
    details = usage.get("input_tokens_details") or {}
    cached = min(total, max(0, int(details.get("cached_tokens", 0))))
    uncached = total - cached
    writes = min(uncached, max(0, int(details.get("cache_write_tokens", 0))))
    output = max(0, int(usage.get("output_tokens", 0)))
    return calculate_cost_usd(model, uncached, output, cached) + (
        0.25 * calculate_cost_usd(model, writes, 0)
    )
