from bingo.memory import LayeredMemory


def test_working_memory_tracks_summary_and_recent_files():
    memory = LayeredMemory()

    memory.set_task_summary("Investigate flaky tests")
    memory.remember_file("README.md")
    memory.remember_file("src/app.py")
    memory.remember_file("README.md")

    snapshot = memory.to_dict()

    assert snapshot["working"]["task_summary"] == "Investigate flaky tests"
    assert snapshot["working"]["recent_files"] == ["src/app.py", "README.md"]
    assert snapshot["task"] == "Investigate flaky tests"
    assert snapshot["files"] == ["src/app.py", "README.md"]


def test_rendered_working_memory_does_not_expand_file_summaries(tmp_path):
    path = tmp_path / "sample.py"
    path.write_text("SECRET_CODE_DETAIL = 1\n", encoding="utf-8")
    memory = LayeredMemory(workspace_root=tmp_path)
    memory.remember_file("sample.py")
    memory.set_file_summary("sample.py", "SECRET_CODE_DETAIL from previous read")

    rendered = memory.render_memory_text()

    assert "sample.py" in rendered
    assert "SECRET_CODE_DETAIL" not in rendered


def test_rendered_working_state_does_not_repeat_current_task_text():
    memory = LayeredMemory()
    memory.set_task_summary("fix the current request")

    rendered = memory.render_memory_text()

    assert rendered.startswith("Working state:")
    assert "fix the current request" not in rendered


def test_recall_excludes_legacy_file_read_notes_but_keeps_decisions(tmp_path):
    (tmp_path / "retrieval.py").write_text("def search():\n    pass\n", encoding="utf-8")
    memory = LayeredMemory(workspace_root=tmp_path)
    memory.append_note(
        "search reads every source file",
        tags=("search",),
        source="retrieval.py",
    )
    memory.append_note(
        "Decision: use short symbol cards",
        tags=("search", "decision"),
        source="key-decisions",
        kind="durable",
    )

    candidates = memory.retrieval_candidates("search decision", limit=4)

    assert [item["text"] for item in candidates] == ["Decision: use short symbol cards"]


def test_memory_recall_tokenizes_chinese_terms():
    memory = LayeredMemory()
    memory.append_note("向量卡只保存符号身份和行为摘要", tags=("架构决定",))

    candidates = memory.retrieval_candidates("之前的架构决定是什么", limit=3)

    assert candidates


def test_memory_recall_dedupes_session_and_durable_copies(tmp_path):
    memory_root = tmp_path / ".bingo" / "memory"
    topics = memory_root / "topics"
    topics.mkdir(parents=True)
    (memory_root / "MEMORY.md").write_text(
        "# Durable Memory Index\n\n"
        "- [key-decisions](topics/key-decisions.md): Key Decisions\n"
        "  - summary: Stable decisions.\n"
        "  - tags: decision\n",
        encoding="utf-8",
    )
    (topics / "key-decisions.md").write_text(
        "# Key Decisions\n\n- tags: decision\n- updated_at: 2026-09-08T00:00:00+00:00\n\n"
        "## Notes\n- Keep symbol cards short.\n",
        encoding="utf-8",
    )
    memory = LayeredMemory(workspace_root=tmp_path)
    memory.append_note(
        "Keep symbol cards short.", tags=("decision",), source="key-decisions", kind="durable"
    )

    candidates = memory.retrieval_candidates("symbol decision", limit=4)

    assert [item["text"] for item in candidates] == ["Keep symbol cards short."]


def test_episodic_notes_append_and_retrieve_deterministically():
    memory = LayeredMemory()

    memory.append_note("Exact tag note", tags=("recall",), created_at="2026-04-07T10:00:00+00:00")
    memory.append_note("Keyword overlap note about memory", created_at="2026-04-07T10:01:00+00:00")
    memory.append_note("Newest unrelated note", created_at="2026-04-07T10:02:00+00:00")
    memory.append_note("Older unrelated note", created_at="2026-04-07T09:59:00+00:00")

    snapshot = memory.to_dict()
    assert [note["text"] for note in snapshot["episodic_notes"]] == [
        "Exact tag note",
        "Keyword overlap note about memory",
        "Newest unrelated note",
        "Older unrelated note",
    ]
    assert snapshot["notes"] == [
        "Exact tag note",
        "Keyword overlap note about memory",
        "Newest unrelated note",
        "Older unrelated note",
    ]

    lines = [line for line in memory.retrieval_view("recall memory", limit=4).splitlines() if line.startswith("- ")]
    assert lines == [
        "- Exact tag note",
        "- Keyword overlap note about memory",
    ]


def test_file_summaries_use_canonical_paths_and_freshness(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("alpha\n", encoding="utf-8")
    memory = LayeredMemory(workspace_root=tmp_path)

    memory.set_file_summary("./sample.txt", "sample.txt: alpha")
    memory.remember_file("./sample.txt")
    snapshot = memory.to_dict()["file_summaries"]["sample.txt"]

    assert snapshot["summary"] == "sample.txt: alpha"
    assert snapshot["freshness"]

    assert "sample.txt" in memory.render_memory_text()
    assert "sample.txt: alpha" not in memory.render_memory_text()
    file_path.write_text("beta\n", encoding="utf-8")
    assert "sample.txt: alpha" not in memory.render_memory_text()

    memory.invalidate_file_summary("sample.txt")

    assert "sample.txt" not in memory.to_dict()["file_summaries"]


def test_process_notes_keep_kind_and_latest_duplicate_wins():
    memory = LayeredMemory()

    memory.append_note(
        "Shell partial success on README.md; inspect diff before retry",
        tags=("process", "partial_success"),
        created_at="2026-04-07T10:00:00+00:00",
        kind="process",
    )
    memory.append_note(
        "Shell partial success on README.md; inspect diff before retry",
        tags=("process", "partial_success"),
        created_at="2026-04-07T10:01:00+00:00",
        kind="process",
    )

    notes = memory.to_dict()["episodic_notes"]

    assert len(notes) == 1
    assert notes[0]["kind"] == "process"
    assert notes[0]["created_at"] == "2026-04-07T10:01:00+00:00"


def test_durable_memory_index_and_topic_notes_are_loaded_and_retrieved(tmp_path):
    memory_root = tmp_path / ".bingo" / "memory"
    topics_dir = memory_root / "topics"
    topics_dir.mkdir(parents=True)
    (memory_root / "MEMORY.md").write_text(
        "# Durable Memory Index\n\n"
        "- [project-conventions](topics/project-conventions.md): Project Conventions\n"
        "  - summary: Stable repository conventions.\n"
        "  - tags: convention\n",
        encoding="utf-8",
    )
    (topics_dir / "project-conventions.md").write_text(
        "# Project Conventions\n\n"
        "- topic: project-conventions\n"
        "- summary: Stable repository conventions.\n"
        "- tags: convention\n"
        "- updated_at: 2026-04-12T08:14:49+00:00\n\n"
        "## Notes\n"
        "- Use constrained tools instead of guessing.\n"
        "- Preserve local agent state under .bingo/.\n",
        encoding="utf-8",
    )

    memory = LayeredMemory(workspace_root=tmp_path)

    snapshot = memory.to_dict()
    assert snapshot["durable_topics"] == ["project-conventions"]

    lines = [line for line in memory.retrieval_view("constrained tools", limit=4).splitlines() if line.startswith("- ")]
    assert any("Use constrained tools instead of guessing." in line for line in lines)
