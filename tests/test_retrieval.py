import json

import pytest

from bingo.retrieval import RetrievalEngine
from bingo.retrieval_corpus import chunk_file


class FixtureEncoder:
    """Deterministic semantic fixture, never a production embedding backend."""
    identity = "fixture-v1"

    def __init__(self):
        self.calls = []

    def embed(self, texts, query=False):
        self.calls.extend(texts)
        return [[1.0, 0.0] if any(x in text for x in ("authenticate", "登录", "identity")) else [0.0, 1.0] for text in texts]


def repo(tmp_path):
    (tmp_path / "auth.py").write_text('def authenticate(secret):\n    """Check identity."""\n    return bool(secret)\n', encoding="utf-8")
    (tmp_path / "maths.py").write_text('def multiply(a, b):\n    return a * b\n', encoding="utf-8")
    return tmp_path


def test_routes_and_semantic_only_hit(tmp_path):
    engine = RetrievalEngine(repo(tmp_path), encoder=FixtureEncoder())
    direct = engine.search("explain", path="auth.py")
    assert direct["strategy"] == "direct"
    assert direct["hits"][0]["path"] == "auth.py"
    lexical = engine.search("authenticate")
    assert lexical["strategy"] == "symbol"
    semantic = engine.search("在哪里处理登录", mode="hybrid")
    assert semantic["strategy"] == "hybrid"
    assert semantic["hits"][0]["path"] == "auth.py"
    assert "vector" in semantic["hits"][0]["channels"]
    assert semantic["vector_coverage"] == 1.0


def test_incremental_update_delete_and_model_identity(tmp_path):
    encoder = FixtureEncoder()
    engine = RetrievalEngine(repo(tmp_path), encoder=encoder)
    engine.search("登录", mode="hybrid")
    encoder.calls.clear()
    engine.search("登录", mode="hybrid")
    assert encoder.calls == []
    (tmp_path / "auth.py").write_text("def logout():\n    return None\n", encoding="utf-8")
    (tmp_path / "maths.py").unlink()
    result = engine.search("logout", mode="keyword")
    assert result["hits"][0]["symbol"] == "logout"
    assert engine.search("multiply", mode="keyword")["hits"] == []
    encoder.identity = "fixture-v2"
    encoder.calls.clear()
    engine.search("登录", mode="hybrid")
    assert any("logout" in text for text in encoder.calls)


def test_fallback_and_strict_budget(tmp_path):
    class Broken(FixtureEncoder):
        def embed(self, texts, query=False):
            raise RuntimeError("provider error containing secret")
    engine = RetrievalEngine(repo(tmp_path), encoder=Broken())
    result = engine.search("authenticate", mode="hybrid", budget_chars=800, budget_tokens=200)
    assert result["strategy"] == "keyword"
    assert result["fallback_reason"] == "embedding_unavailable"
    assert "secret" not in result["fallback_reason"]
    assert len(result["text"]) <= 800
    assert len(result["text"].encode("utf-8")) <= 200
    tiny = engine.search("authenticate", budget_chars=20, budget_tokens=20)
    assert tiny["text"] == "" and tiny["hits"] == []


def test_ignore_and_path_escape(tmp_path):
    repo(tmp_path)
    (tmp_path / ".gitignore").write_text("private/\n", encoding="utf-8")
    (tmp_path / "private").mkdir()
    (tmp_path / "private/key.py").write_text("authenticate = 'hidden'", encoding="utf-8")
    (tmp_path / ".env").write_text("authenticate=secret", encoding="utf-8")
    engine = RetrievalEngine(tmp_path)
    result = engine.search("authenticate", mode="keyword")
    assert [h["path"] for h in result["hits"]] == ["auth.py"]
    for path in ("../", ".env", "private/key.py", ".bingo"):
        with pytest.raises(ValueError):
            engine.search("authenticate", path=path)


def test_ast_lines_and_no_duplicate_overlap(tmp_path):
    text = "import os\n\n@decorator\ndef run():\n    return 1\n"
    chunks = chunk_file("main.py", text)
    item = next(c for c in chunks if c["symbol"] == "run")
    assert item["start_line"] == 3 and item["end_line"] == 5
    assert item["content"] == "\n".join(text.splitlines()[2:5])
    (tmp_path / "a.py").write_text(text, encoding="utf-8")
    (tmp_path / "b.py").write_text(text, encoding="utf-8")
    result = RetrievalEngine(tmp_path).search("run", mode="keyword")
    functions = [h for h in result["hits"] if h["symbol"] == "run"]
    assert len(functions) == 1
    assert len(functions[0]["sources"]) == 2


def test_size_tiers_and_index_limit_are_observable(tmp_path):
    engine = RetrievalEngine(repo(tmp_path), encoder=FixtureEncoder(), small_chunks=0, large_chunks=1, max_embed_chunks=1)
    result = engine.search("登录", mode="hybrid")
    assert result["corpus_tier"] == "large"
    assert 0 < result["vector_coverage"] < 1
    engine.index(complete=True)
    assert engine.search("登录", mode="hybrid")["vector_coverage"] == 1.0


def test_runtime_tool_and_auto_evidence(tmp_path):
    from bingo import Bingo, FakeModelClient, SessionStore, WorkspaceContext
    engine = RetrievalEngine(repo(tmp_path), encoder=FixtureEncoder())
    agent = Bingo(FakeModelClient(["<final>done</final>"]), WorkspaceContext.build(tmp_path),
                  SessionStore(tmp_path / ".bingo/sessions"), retrieval_engine=engine, auto_retrieve=True)
    result = agent.run_tool("retrieve_code", {"query": "authenticate"})
    assert "auth.py:1-3" in result
    assert agent._last_tool_result_metadata["retrieval"]["strategy"] == "symbol"
    prompt, metadata = agent.context_manager.build("在哪里处理登录")
    assert "auth.py:1-3" in prompt
    assert metadata["retrieval"]["strategy"] == "hybrid"


def test_nested_ignore_negation_and_source_scope(tmp_path):
    repo(tmp_path)
    sub = tmp_path / "module"
    sub.mkdir()
    (sub / ".gitignore").write_text("*.py\n!keep.py\n", encoding="utf-8")
    (sub / "keep.py").write_text("def authenticate():\n    return True\n", encoding="utf-8")
    (sub / "hidden.py").write_text("def authenticate():\n    return False\n", encoding="utf-8")
    result = RetrievalEngine(tmp_path).search("authenticate", path="module", mode="keyword")
    assert [h["path"] for h in result["hits"]] == ["module/keep.py"]


def test_file_renamed_and_persistent_vector_cache(tmp_path):
    encoder = FixtureEncoder()
    engine = RetrievalEngine(repo(tmp_path), encoder=encoder)
    engine.index(complete=True)
    engine.close()
    encoder.calls.clear()
    engine = RetrievalEngine(tmp_path, encoder=encoder)
    assert engine.index(complete=True)["embedded_chunks"] == 0
    assert encoder.calls == []
    (tmp_path / "auth.py").rename(tmp_path / "login.py")
    result = engine.search("authenticate", mode="keyword")
    assert result["hits"][0]["path"] == "login.py"
    assert result["corpus"]["deleted_files"] == 1


def test_no_answer_and_changed_vector_dimension(tmp_path):
    encoder = FixtureEncoder()
    engine = RetrievalEngine(repo(tmp_path), encoder=encoder)
    assert not engine.search("not_a_real_symbol", mode="keyword")["hits"]
    engine.index(complete=True)
    encoder.embed = lambda texts, query=False: [[1, 2, 3] for _ in texts]
    result = engine.search("authenticate", mode="hybrid")
    assert result["strategy"] == "keyword"
    assert result["fallback_reason"] == "embedding_unavailable"


def test_source_modified_during_embedding_is_not_returned(tmp_path):
    class Mutating(FixtureEncoder):
        def embed(self, texts, query=False):
            if query:
                (tmp_path / "auth.py").write_text("def replaced():\n    pass\n", encoding="utf-8")
            return super().embed(texts, query)
    result = RetrievalEngine(repo(tmp_path), encoder=Mutating()).search("登录", mode="hybrid")
    assert not result["hits"]
    assert result["stale_candidates"] == 1


def test_runtime_preserves_compact_retrieval_cards_in_history(tmp_path):
    from bingo import Bingo, FakeModelClient, SessionStore, WorkspaceContext
    repo(tmp_path)
    model = FakeModelClient(['<tool>{"name":"retrieve_code","args":{"query":"authenticate"}}</tool>', '<final>done</final>'])
    agent = Bingo(model, WorkspaceContext.build(tmp_path), SessionStore(tmp_path / ".bingo/sessions"))
    assert agent.ask("find authenticate") == "done"
    entry = next(e for e in agent.session["history"] if e["role"] == "tool")
    assert entry["retrieval_hits"][0]["path"] == "auth.py"
    rendered = agent.context_manager._render_history_section(5000).rendered
    assert "symbol_id=" in rendered
    assert 'return bool(secret)' not in rendered
    tiny = agent.context_manager._render_history_section(100).rendered
    assert "Retrieved code" not in tiny


def test_http_embedding_request_and_response_validation(monkeypatch):
    from io import BytesIO

    from bingo.embeddings import HTTPEncoder
    captured = []
    def respond(request, timeout):
        captured.append((request, timeout))
        return BytesIO(json.dumps({"data": [{"index": 1, "embedding": [0, 2]}, {"index": 0, "embedding": [3, 0]}]}).encode())
    monkeypatch.setattr("urllib.request.urlopen", respond)
    encoder = HTTPEncoder("openai", "embedding-model", "https://example.invalid/v1", "test-key", timeout=3)
    assert encoder.embed(["one", "two"]) == [[1, 0], [0, 1]]
    assert captured[0][0].full_url.endswith("/v1/embeddings")
    assert json.loads(captured[0][0].data)["input"] == ["one", "two"]
    assert captured[0][1] == 3
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: BytesIO(b'{"data": [{"index": 0,"embedding": [0,0]}]}'))
    with pytest.raises(RuntimeError, match="invalid vectors"):
        encoder.embed(["one"])


def test_ollama_transport_and_vector_validation(monkeypatch):
    from io import BytesIO

    from bingo.embeddings import HTTPEncoder, normalize_vectors
    def respond(request, timeout):
        assert request.full_url == "http://localhost:11434/api/embed"
        assert json.loads(request.data)["truncate"] is False
        return BytesIO(b'{"embeddings": [[1,0]]}')
    monkeypatch.setattr("urllib.request.urlopen", respond)
    assert HTTPEncoder("ollama", "model", "http://localhost:11434").embed(["code"]) == [[1, 0]]
    for value in ([[float("nan")]], [[1, 2], [1]], [[0, 0]]):
        with pytest.raises(ValueError):
            normalize_vectors(value, len(value))


def test_large_ann_backend_and_update(tmp_path):
    pytest.importorskip("usearch")
    engine = RetrievalEngine(repo(tmp_path), encoder=FixtureEncoder(), small_chunks=0, large_chunks=1)
    result = engine.search("登录", mode="hybrid")
    assert result["vector_backend"] == "usearch_hnsw"
    assert result["hits"][0]["path"] == "auth.py"
    (tmp_path / "auth.py").unlink()
    assert engine.search("登录", mode="vector")["hits"] == []


def test_auto_prompt_budget_drops_whole_blocks_and_skips_chat(tmp_path):
    from bingo import Bingo, FakeModelClient, SessionStore, WorkspaceContext
    from bingo.context_manager import ContextManager
    encoder = FixtureEncoder()
    agent = Bingo(FakeModelClient([]), WorkspaceContext.build(repo(tmp_path)), SessionStore(tmp_path / ".bingo/sessions"),
                  retrieval_engine=RetrievalEngine(tmp_path, encoder=encoder), auto_retrieve=True)
    manager = ContextManager(agent, total_budget=2600)
    prompt, metadata = manager.build("在哪里处理登录")
    assert len(prompt) <= 2600
    assert metadata["retrieval"]["rendered_chars"] <= 2400
    if "auth.py:1-3" in prompt:
        assert "return bool(secret)" in prompt
    encoder.calls.clear()
    manager.build("你好")
    assert encoder.calls == []


def test_direct_keeps_full_file_and_all_fitting_files(tmp_path):
    (tmp_path / "sample.txt").write_text("\n".join(f"line_{n}" for n in range(60)), encoding="utf-8")
    engine = RetrievalEngine(tmp_path)
    result = engine.search("overview", path="sample.txt")
    assert result["strategy"] == "direct"
    assert "line_59" in result["text"]
    assert result["hits"][0]["end_line"] == 60
    for n in range(10):
        (tmp_path / f"extra{n}.txt").write_text(f"unique {n}", encoding="utf-8")
    result = engine.search("overview", mode="direct", top_k=1, budget_chars=8000, budget_tokens=8000)
    assert len(result["hits"]) == 11


def test_windows_junction_never_enters_embedding_input(tmp_path):
    import os
    import subprocess
    if os.name != "nt":
        pytest.skip("Windows junction regression")
    root = tmp_path / "repo"
    root.mkdir()
    repo(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.py").write_text("OUTSIDESECRETXYZ = 'must not embed'", encoding="utf-8")
    junction = root / "linked"
    created = subprocess.run([os.environ.get("COMSPEC", "cmd.exe"), "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True, check=False)
    if created.returncode:
        pytest.skip("junction creation unavailable")
    try:
        encoder = FixtureEncoder()
        engine = RetrievalEngine(root, encoder=encoder)
        engine.index(complete=True)
        assert all("OUTSIDESECRETXYZ" not in text for text in encoder.calls)
        assert engine.search("OUTSIDESECRETXYZ", mode="keyword")["hits"] == []
        engine.close()
    finally:
        junction.rmdir()


def test_many_duplicates_keep_all_sources_without_crowding_budget(tmp_path):
    for n in range(60):
        (tmp_path / f"copy_{n}.py").write_text("def authenticate():\n    return True\n", encoding="utf-8")
    result = RetrievalEngine(tmp_path).search("authenticate", mode="keyword", budget_chars=1000, budget_tokens=1000)
    assert len(result["hits"]) == 1
    assert len(result["hits"][0]["sources"]) == 60
    assert "symbol_id=" in result["text"]
    assert "return True" not in result["text"]


def test_low_similarity_ann_uses_exact_fallback(tmp_path):
    pytest.importorskip("usearch")
    class NearQuery(FixtureEncoder):
        def embed(self, texts, query=False):
            return [[1, .2] for _ in texts] if query else super().embed(texts)
    engine = RetrievalEngine(repo(tmp_path), encoder=NearQuery(), small_chunks=0, large_chunks=1, ann_min_similarity=.99)
    result = engine.search("登录", mode="vector")
    assert result["vector_backend"] == "exact_cosine"
    assert result["ann_fallback_reason"] == "ann_low_similarity"
    assert result["hits"][0]["path"] == "auth.py"
