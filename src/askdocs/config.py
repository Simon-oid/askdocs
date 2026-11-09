"""Settings, from environment variables prefixed ASKDOCS_ (or a .env file).

Every limit the service enforces lives here, with its reason, so none is a
magic number in the code that uses it."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .telemetry import Prices

PROVIDER_BASE_URL = {
    "llamacpp": "http://localhost:8081/v1",
    "openai": "https://api.openai.com/v1",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASKDOCS_", env_file=".env", extra="ignore")

    # --- LLM -------------------------------------------------------------
    # Both providers speak the OpenAI chat-completions wire format; they
    # differ in defaults and in whether a key is required.
    llm_provider: Literal["llamacpp", "openai"] = "llamacpp"
    llm_base_url: str | None = None     # None: the provider's default below
    llm_model: str = "local"            # llama.cpp serves one model and ignores the name
    llm_api_key: SecretStr | None = None
    llm_max_tokens: int = Field(512, ge=16, le=4096)
    llm_temperature: float = Field(0.0, ge=0.0, le=2.0)   # 0: reproducible evals
    llm_connect_timeout_s: float = 5.0
    # Max wait for the *next* streamed chunk, not the whole answer: a slow
    # but progressing answer is fine, a stalled one is not.
    llm_read_timeout_s: float = 60.0
    # ...and a cap on the whole answer, since keep-alives reset the one above.
    llm_total_timeout_s: float = 180.0
    # Retries happen only before the first token reaches the client; after
    # that a retry would duplicate text the client has already shown.
    llm_max_retries: int = Field(2, ge=0, le=5)
    llm_retry_backoff_s: float = 0.5
    # USD per million tokens, for cost per answer. 0 for a local model,
    # whose cost is GPU time; set them to the provider's list prices.
    llm_price_input_per_mtok: float = Field(0.0, ge=0.0)
    llm_price_output_per_mtok: float = Field(0.0, ge=0.0)

    # --- Retrieval (crag-mcp over stdio) -----------------------------------
    mcp_command: str = "crag-mcp"
    mcp_db: str = "data/docs.db"
    embed_backend: str = "ollama"
    embed_model: str = "mxbai-embed-large"
    embed_url: str = "http://localhost:11434/api/embeddings"
    mcp_timeout_s: float = 10.0
    mcp_reconnect_backoff_s: float = 0.5
    top_k: int = Field(5, ge=1, le=20)
    # Shown to the client and the model instead of the indexer's absolute
    # paths: everything up to and including this marker is cut.
    source_root_marker: str = "corpus/"

    # --- Service ---------------------------------------------------------
    # One GPU serves one generation well; past this many in-flight answers
    # requests are refused with 503 rather than queued without bound.
    max_concurrent: int = Field(4, ge=1, le=256)

    # --- Observability ---------------------------------------------------
    # OTLP/HTTP collector base URL (Jaeger, Tempo, OTel Collector), e.g.
    # http://localhost:4318. Unset: no tracing, and the SDK is not needed.
    otel_endpoint: str | None = None
    otel_service_name: str = "askdocs"

    def llm_key(self) -> str | None:
        return self.llm_api_key.get_secret_value() if self.llm_api_key else None

    def prices(self) -> Prices:
        return Prices(self.llm_price_input_per_mtok, self.llm_price_output_per_mtok)

    def base_url(self) -> str:
        return self.llm_base_url or PROVIDER_BASE_URL[self.llm_provider]

    def check(self) -> None:
        """Fail at startup, not on the first request."""
        if self.llm_provider == "openai":
            if not self.llm_api_key:
                raise ValueError("ASKDOCS_LLM_API_KEY is required for the openai provider")
            if self.llm_model == "local":
                raise ValueError("ASKDOCS_LLM_MODEL must name a model for the openai provider")
