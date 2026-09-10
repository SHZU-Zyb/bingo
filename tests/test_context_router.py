from bingo.context_router import route_context


def test_context_router_selects_code_for_repository_question():
    route = route_context("retrieval.py 里的 search 方法怎么实现", auto_retrieve=True)
    assert route["use_code_retrieval"] is True
    assert route["use_memory_recall"] is False
    assert route["intent"] == "code"


def test_context_router_selects_memory_for_prior_decision():
    route = route_context("我们之前为什么决定不嵌入完整代码", auto_retrieve=True)
    assert route["use_code_retrieval"] is False
    assert route["use_memory_recall"] is True
    assert route["intent"] == "memory"


def test_context_router_selects_both_for_mixed_question():
    route = route_context(
        "按照之前的架构决定修改 retrieval.py 的代码", auto_retrieve=True
    )
    assert route["use_code_retrieval"] is True
    assert route["use_memory_recall"] is True
    assert route["intent"] == "mixed"


def test_context_router_can_recall_a_strong_candidate_without_memory_cue():
    route = route_context(
        "vector cards",
        auto_retrieve=False,
        memory_candidate_count=1,
    )
    assert route["use_code_retrieval"] is False
    assert route["use_memory_recall"] is True
    assert "memory_candidate_match" in route["reasons"]


def test_context_router_honors_feature_switches():
    route = route_context(
        "按照之前的决定修改函数",
        auto_retrieve=True,
        memory_enabled=False,
        relevant_memory_enabled=False,
    )
    assert route["use_memory_recall"] is False
    assert route["use_code_retrieval"] is True
