from openai import AsyncOpenAI

from agent_chassis.config import get_settings


class OpenAIEmbedder:
    """text-embedding via OpenAI."""

    def __init__(self, model: str | None = None, dim: int | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.embedding_model
        self.dim = dim or settings.embedding_dim
        self._client = AsyncOpenAI()

    async def embed(self, text: str) -> list[float]:
        response = await self._client.embeddings.create(
            model=self.model, input=text, dimensions=self.dim
        )
        return response.data[0].embedding
