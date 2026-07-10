"""Thin async Anthropic client for the AI analyst.

Streams a narration from Claude over an engine-computed context block. This is
the ONLY module here that makes a network call; it is deliberately small and is
exercised end-to-end (needs a live key), not in unit tests. All grounding,
prompt building and cost math live in the pure `context` module beside it.

Guardrails honored here: the API key is read from Windows Credential Manager via
`data.credentials` and never logged or echoed; the request carries only the
engine-computed context the caller passes in, never raw key material; and a call
happens only when the caller invokes `stream()` (the panel wires it to a button).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from cqd.analyst import context

DEFAULT_MODEL = "claude-opus-4-8"
_MAX_TOKENS = 1024


class AnalystError(Exception):
    """Analyst call failed (no key, network, or API error). Message is safe to
    show; it never contains key material."""


@dataclass(frozen=True)
class AnalystResult:
    """What a completed stream produced: token usage for the cost line."""

    input_tokens: int
    output_tokens: int
    model: str

    @property
    def cost_line(self) -> str:
        return context.format_cost(self.model, self.input_tokens, self.output_tokens)


class AnalystClient:
    """Wraps `anthropic.AsyncAnthropic` for streamed, grounded narration."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        if not api_key:
            raise AnalystError("No Anthropic API key configured.")
        self._api_key = api_key
        self.model = model

    async def stream(
        self,
        mode: str,
        engine_context: dict,
        on_delta: Callable[[str], None] | Callable[[str], Awaitable[None]],
        question: str | None = None,
    ) -> AnalystResult:
        """Stream a narration, pushing text chunks to `on_delta` as they arrive.

        Returns token usage once the stream completes. Raises AnalystError with a
        key-free message on any failure so the panel can show it inline.
        """
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover - dependency is pinned
            raise AnalystError("The 'anthropic' package is not installed.") from e

        user = context.build_user_message(mode, engine_context, question)
        client = anthropic.AsyncAnthropic(api_key=self._api_key)
        try:
            async with client.messages.stream(
                model=self.model,
                max_tokens=_MAX_TOKENS,
                system=context.SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user}],
            ) as stream:
                async for text in stream.text_stream:
                    result = on_delta(text)
                    if result is not None:
                        await result
                final = await stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise AnalystError("Anthropic rejected the API key. Check it in Settings.") from e
        except anthropic.APIStatusError as e:
            raise AnalystError(f"Anthropic API error ({e.status_code}).") from e
        except anthropic.APIError as e:
            raise AnalystError(f"Could not reach Anthropic: {e.__class__.__name__}.") from e
        finally:
            await client.close()

        usage = final.usage
        return AnalystResult(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            model=self.model,
        )
