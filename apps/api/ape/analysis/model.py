"""The only place that knows which LLM provider is in use.

Switching from Claude to a local vLLM or Ollama server is a matter of
LLM_PROVIDER, LLM_BASE_URL and LLM_MODEL; no analysis code changes.
"""

from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from ape.core.config import Settings


def build_model(settings: Settings) -> Model | None:
    """Returns the configured model, or None when LLM enrichment is switched off."""
    match settings.llm_provider:
        case "" | "none":
            return None
        case "anthropic":
            return AnthropicModel(
                settings.llm_model, provider=AnthropicProvider(api_key=settings.llm_api_key or None)
            )
        case "openai_compatible":  # vLLM, Ollama, LiteLLM, OpenAI
            return OpenAIChatModel(
                settings.llm_model,
                provider=OpenAIProvider(
                    base_url=settings.llm_base_url or None, api_key=settings.llm_api_key or "none"
                ),
            )
        case other:
            raise ValueError(f"unknown LLM_PROVIDER: {other}")
