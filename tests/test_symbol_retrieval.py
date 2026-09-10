import pytest

from bingo.retrieval import RetrievalEngine
from bingo.retrieval_corpus import CorpusIndex, extract_symbols, symbol_cards


class CardEncoder:
    identity = "card-fixture-v1"

    def __init__(self):
        self.documents = []
        self.queries = []

    def embed(self, texts, query=False):
        target = self.queries if query else self.documents
        target.extend(texts)
        vectors = []
        for text in texts:
            semantic = any(word in text.lower() for word in ("authenticate", "identity", "登录", "session"))
            vectors.append([1.0, 0.0] if semantic else [0.0, 1.0])
        return vectors


SOURCE = '''\
class BaseStore:
    pass

class SessionStore(BaseStore):
    """Persist local sessions and recover the newest entry."""

    def __init__(self, root):
        self.root = root

    def latest(self):
        """Return the latest session file."""
        files = self.root.glob("*.json")
        def modified(path):
            return path.stat().st_mtime
        return max(files, key=modified).stem

def authenticate(secret):
    password = "super-secret-value"
    return bool(secret and password)

def login_user(token):
    return authenticate(token)
'''


def test_ast_symbol_graph_has_multiple_granularities_and_stable_metadata():
    symbols, edges = extract_symbols("session.py", SOURCE)
    by_name = {row["qualified_name"]: row for row in symbols}

    assert {"BaseStore", "SessionStore", "SessionStore.__init__", "SessionStore.latest",
            "SessionStore.latest.modified", "authenticate", "login_user"} <= set(by_name)
    latest = by_name["SessionStore.latest"]
    assert latest["kind"] == "method"
    assert latest["parent_qualified_name"] == "SessionStore"
    assert latest["signature"] == "latest(self)"
    assert latest["start_line"] < latest["end_line"]
    assert latest["symbol_id"] == extract_symbols("session.py", "\n" + SOURCE)[0][3]["symbol_id"]

    edge_set = {(e["source_qualified_name"], e["relation"], e["target_qualified_name"]) for e in edges}
    assert ("SessionStore", "inherits", "BaseStore") in edge_set
    assert ("SessionStore", "contains", "SessionStore.latest") in edge_set
    assert ("login_user", "calls", "authenticate") in edge_set


def test_symbol_cards_are_short_deterministic_and_exclude_source_body():
    symbols, _ = extract_symbols("session.py", SOURCE)
    authenticate = next(row for row in symbols if row["qualified_name"] == "authenticate")
    cards = symbol_cards(authenticate)

    assert set(cards) == {"identity", "behavior"}
    assert "qualified_name: authenticate" in cards["identity"]
    assert "return bool" in cards["behavior"]
    combined = "\n".join(cards.values())
    assert "super-secret-value" not in combined
    assert "password =" not in combined
    assert "symbol_id" not in combined and "content_hash" not in combined
    assert all(len(card) <= 1200 for card in cards.values())


def test_corpus_persists_symbol_index_and_exact_search(tmp_path):
    (tmp_path / "session.py").write_text(SOURCE, encoding="utf-8")
    corpus = CorpusIndex(tmp_path)
    stats = corpus.sync()

    assert stats["symbols"] == 7
    exact = corpus.symbol_search("SessionStore.latest")
    assert exact[0]["qualified_name"] == "SessionStore.latest"
    assert exact[0]["path"] == "session.py"
    assert corpus.graph_neighbors(exact[0]["symbol_id"], relations={"contains"})


def test_auto_router_uses_symbol_then_escalates_semantic_query(tmp_path):
    (tmp_path / "session.py").write_text(SOURCE, encoding="utf-8")
    encoder = CardEncoder()
    engine = RetrievalEngine(tmp_path, encoder=encoder)

    exact = engine.search("SessionStore.latest")
    assert exact["strategy"] == "symbol"
    assert exact["route_reason"] == "qualified_symbol_query"
    assert exact["hits"][0]["qualified_name"] == "SessionStore.latest"
    assert "return max" not in exact["text"]
    assert "symbol_id=" in exact["text"]

    semantic = engine.search("在哪里恢复最新的登录会话")
    assert semantic["strategy"] == "hybrid"
    assert semantic["escalated"] is True
    assert {"symbol", "keyword", "vector"} <= set(semantic["channels_used"])
    assert semantic["vector_coverage"] == 1.0
    assert encoder.documents
    assert all("super-secret-value" not in card for card in encoder.documents)
    assert all(len(card) <= 1200 for card in encoder.documents)


def test_semantic_router_does_not_treat_unrelated_token_hits_as_strong_evidence(tmp_path):
    (tmp_path / "vector.py").write_text("def vector_value():\n    return 1\n", encoding="utf-8")
    (tmp_path / "evidence.py").write_text("def evidence_value():\n    return 2\n", encoding="utf-8")
    engine = RetrievalEngine(tmp_path, encoder=CardEncoder())

    result = engine.search("where should vector evidence be combined")

    assert result["strategy"] == "hybrid"
    assert result["escalated"] is True
    assert result["route_reason"] == "weak_first_pass_escalation"


def test_graph_query_expands_callers_with_bounded_trace(tmp_path):
    (tmp_path / "session.py").write_text(SOURCE, encoding="utf-8")
    engine = RetrievalEngine(tmp_path)
    result = engine.search("who calls authenticate")

    assert result["query_type"] == "relationship"
    assert any(hit["qualified_name"] == "login_user" for hit in result["hits"])
    assert "graph" in result["channels_used"]
    assert result["graph_depth"] <= 1


def test_read_symbol_verifies_hash_and_returns_exact_current_source(tmp_path):
    path = tmp_path / "session.py"
    path.write_text(SOURCE, encoding="utf-8")
    engine = RetrievalEngine(tmp_path)
    hit = engine.search("SessionStore.latest")["hits"][0]

    read = engine.read_symbol(hit["symbol_id"])
    assert read["qualified_name"] == "SessionStore.latest"
    assert "def latest(self):" in read["content"]
    assert "def authenticate" not in read["content"]

    path.write_text(SOURCE.replace("return max", "return min"), encoding="utf-8")
    with pytest.raises(ValueError, match="stale"):
        engine.read_symbol(hit["symbol_id"], expected_hash=hit["content_hash"])


def test_syntax_error_keeps_chunk_fallback_without_fake_symbols(tmp_path):
    (tmp_path / "broken.py").write_text("def unfinished(:\n", encoding="utf-8")
    corpus = CorpusIndex(tmp_path)
    stats = corpus.sync()

    assert stats["symbols"] == 0
    assert stats["parse_failures"] == 1
    assert corpus.sync()["parse_failures"] == 1
    assert corpus.keyword("unfinished")[0]["path"] == "broken.py"


def test_symbol_graph_resolves_unique_cross_file_calls(tmp_path):
    (tmp_path / "auth.py").write_text("def authenticate(token):\n    return bool(token)\n", encoding="utf-8")
    (tmp_path / "login.py").write_text(
        "from auth import authenticate\n\ndef login_user(token):\n    return authenticate(token)\n", encoding="utf-8")
    corpus = CorpusIndex(tmp_path)
    corpus.sync()
    target = corpus.symbol_search("authenticate")[0]
    neighbors = corpus.graph_neighbors(target["symbol_id"], relations={"calls"})

    assert any(row["qualified_name"] == "login_user" and row["direction"] == "in" for row in neighbors)


def test_read_symbol_tool_expands_a_selected_location(tmp_path):
    from bingo import Bingo, FakeModelClient, SessionStore, WorkspaceContext

    (tmp_path / "session.py").write_text(SOURCE, encoding="utf-8")
    engine = RetrievalEngine(tmp_path)
    hit = engine.search("SessionStore.latest")["hits"][0]
    agent = Bingo(FakeModelClient([]), WorkspaceContext.build(tmp_path),
                  SessionStore(tmp_path / ".bingo/sessions"), retrieval_engine=engine)

    result = agent.run_tool("read_symbol", {"symbol_id": hit["symbol_id"],
                                             "expected_hash": hit["content_hash"], "max_chars": 2000})
    assert "session.py:" in result
    assert "def latest(self):" in result
    assert "def authenticate" not in result
    cached = agent.session["evidence_cache"]["entries"]
    assert len(cached) == 1
    assert cached[0]["symbol_id"] == hit["symbol_id"]
    assert cached[0]["path"] == "session.py"


def test_read_symbol_keeps_line_boundaries_under_budget(tmp_path):
    body = "def large():\n" + "".join(f"    value_{number} = {number}\n" for number in range(100))
    (tmp_path / "large.py").write_text(body, encoding="utf-8")
    engine = RetrievalEngine(tmp_path)
    hit = engine.search("large")["hits"][0]
    result = engine.read_symbol(hit["symbol_id"], max_chars=256)

    assert result["truncated"] is True
    assert len(result["content"]) <= 256
    assert result["content"].splitlines()[-1].lstrip().startswith("value_")
    with pytest.raises(ValueError, match="missing or stale"):
        engine.read_symbol("f" * 64)
