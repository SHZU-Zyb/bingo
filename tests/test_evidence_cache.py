from bingo.evidence_cache import EvidenceCache


def test_evidence_cache_reuses_only_fresh_covering_ranges(tmp_path):
    path = tmp_path / "sample.py"
    path.write_text("def alpha():\n    return 1\n", encoding="utf-8")
    cache = EvidenceCache({}, workspace_root=tmp_path)

    cache.store(
        "sample.py",
        start_line=1,
        end_line=2,
        content="# sample.py\n   1: def alpha():\n   2:     return 1",
        symbol_id="symbol-alpha",
    )

    matches = cache.match_sources(
        [
            {
                "path": "sample.py",
                "start_line": 1,
                "end_line": 2,
                "symbol_id": "symbol-alpha",
            }
        ]
    )
    assert len(matches) == 1
    assert matches[0]["cache_key"]
    assert matches[0]["content"].startswith("# sample.py")

    path.write_text("def alpha():\n    return 2\n", encoding="utf-8")
    assert (
        cache.match_sources([{"path": "sample.py", "start_line": 1, "end_line": 2}])
        == []
    )
    assert cache.to_dict()["entries"] == []


def test_evidence_cache_requires_cached_range_to_cover_candidate(tmp_path):
    path = tmp_path / "sample.py"
    path.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    cache = EvidenceCache({}, workspace_root=tmp_path)
    cache.store("sample.py", 2, 3, "two\nthree")

    assert cache.match_sources([{"path": "sample.py", "start_line": 2, "end_line": 3}])
    assert (
        cache.match_sources([{"path": "sample.py", "start_line": 1, "end_line": 3}])
        == []
    )


def test_evidence_cache_is_lru_bounded_and_invalidates_a_path(tmp_path):
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text(name, encoding="utf-8")
    cache = EvidenceCache({}, workspace_root=tmp_path, max_entries=2)
    cache.store("a.py", 1, 1, "a")
    cache.store("b.py", 1, 1, "b")
    assert cache.lookup("a.py", 1, 1)
    cache.store("c.py", 1, 1, "c")

    assert cache.lookup("a.py", 1, 1)
    assert cache.lookup("b.py", 1, 1) is None
    assert cache.invalidate_path("a.py") == 1
    assert cache.lookup("a.py", 1, 1) is None


def test_evidence_cache_bounds_oversized_legacy_session_state(tmp_path):
    paths = []
    for index in range(3):
        path = tmp_path / f"{index}.py"
        path.write_text(f"value = {index}\n", encoding="utf-8")
        paths.append(path)
    state = {"entries": [], "next_access_index": 3}
    for index, path in enumerate(paths):
        import hashlib

        state["entries"].append(
            {
                "path": path.name,
                "start_line": 1,
                "end_line": 1,
                "content": "X" * 1000,
                "file_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
                "access_index": index,
            }
        )

    cache = EvidenceCache(
        state, workspace_root=tmp_path, max_entries=2, max_content_chars=256
    )

    assert [entry["path"] for entry in cache.to_dict()["entries"]] == ["1.py", "2.py"]
    assert all(len(entry["content"]) == 256 for entry in cache.to_dict()["entries"])
    assert all(entry["complete"] is False for entry in cache.to_dict()["entries"])
