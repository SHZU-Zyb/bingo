"""Real embedding backends. Credentials and remote error bodies never enter traces."""

import hashlib
import json
import math
import os
import urllib.request


def normalize_vectors(vectors, count):
    if len(vectors) != count:
        raise ValueError("embedding count mismatch")
    result, dimension = [], None
    for vector in vectors:
        values = [float(x) for x in vector]
        dimension = dimension or len(values)
        norm = math.sqrt(sum(x*x for x in values))
        if not dimension or len(values) != dimension or not math.isfinite(norm) or norm == 0:
            raise ValueError("invalid embedding shape or values")
        result.append([x/norm for x in values])
    return result


class HTTPEncoder:
    def __init__(self, provider, model, base_url, api_key="", timeout=15):
        if provider not in {"ollama", "openai"}:
            raise ValueError("unsupported embedding provider")
        self.provider, self.model, self.base_url = provider, model, base_url.rstrip("/")
        self.api_key, self.timeout = api_key, timeout
        # Endpoint fingerprint separates different deployments of a model without leaking URLs.
        endpoint = hashlib.sha256(self.base_url.encode()).hexdigest()[:16]
        self.identity = f"{provider}:{model}:{endpoint}:v1"

    def embed(self, texts, query=False):
        payload = {"model": self.model, "input": list(texts)}
        if self.provider == "ollama":
            payload["truncate"] = False
        request = urllib.request.Request(
            self.base_url + ("/api/embed" if self.provider == "ollama" else "/embeddings"),
            data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
        if self.api_key:
            request.add_header("Authorization", "Bearer " + self.api_key)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.load(response)
            if self.provider == "ollama":
                vectors = data["embeddings"]
            else:
                rows = sorted(data["data"], key=lambda row: row["index"])
                if [row["index"] for row in rows] != list(range(len(texts))):
                    raise ValueError("embedding indices mismatch")
                vectors = [row["embedding"] for row in rows]
            return normalize_vectors(vectors, len(texts))
        except Exception:  # noqa: BLE001 - isolate optional provider failures and redact remote errors.
            raise RuntimeError("embedding provider unavailable or returned invalid vectors") from None


class FastEmbedEncoder:
    def __init__(self, model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", cache_dir=None):
        self.model, self.cache_dir = model, cache_dir
        from importlib.metadata import PackageNotFoundError, version
        try:
            runtime_version = version("fastembed")
        except PackageNotFoundError:
            runtime_version = "uninstalled"
        self.identity = f"fastembed:{runtime_version}:{model}:v1"
        self._encoder = None

    def embed(self, texts, query=False):
        if self._encoder is None:
            from fastembed import TextEmbedding
            self._encoder = TextEmbedding(model_name=self.model, cache_dir=self.cache_dir)
        operation = self._encoder.query_embed if query else self._encoder.passage_embed
        return normalize_vectors(list(operation(list(texts))), len(texts))


def encoder_from_env():
    provider = os.getenv("BINGO_EMBED_PROVIDER", "none").lower()
    model = os.getenv("BINGO_EMBED_MODEL", "")
    if provider == "none":
        return None
    if provider == "fastembed":
        return FastEmbedEncoder(model or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", os.getenv("BINGO_EMBED_CACHE") or None)
    if provider in {"ollama", "openai"}:
        return HTTPEncoder(provider, model or ("embeddinggemma" if provider == "ollama" else "text-embedding-3-small"),
                           os.getenv("BINGO_EMBED_BASE_URL", "http://localhost:11434" if provider == "ollama" else "https://api.openai.com/v1"),
                           os.getenv("BINGO_EMBED_API_KEY", ""), float(os.getenv("BINGO_EMBED_TIMEOUT", "15")))
    raise ValueError("BINGO_EMBED_PROVIDER must be none, ollama, openai, or fastembed")
