from pathlib import Path

import pytest

from bingo import FakeModelClient, MiniAgent, SessionStore, WorkspaceContext
from bingo.context_manager import ContextManager
from bingo.skill_loader import SkillLoader
from bingo.skill_registry import SkillRegistry
from bingo.skill_router import SkillRouter


def write_skill(
    root: Path,
    name="testing",
    description="Run project tests",
    *,
    body=None,
    aliases=None,
    triggers=None,
):
    skill_dir = root / ".bingo" / "skills" / name
    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    aliases = aliases or ["test"]
    triggers = triggers or ["测试", "pytest", "regression"]
    body = body or "# Testing\n\nRun the smallest relevant test first."
    text = (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "version: '1.0'\n"
        "aliases:\n"
        + "".join(f"  - {item}\n" for item in aliases)
        + "triggers:\n"
        + "".join(f"  - {item}\n" for item in triggers)
        + "priority: 50\n"
        + "allowed_tools:\n"
        + "  - read_file\n  - search\n  - run_shell\n"
        + "resources:\n"
        + "  references:\n"
        + "    - references/pytest.md\n"
        + "---\n"
        + body
        + "\n"
    )
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    (skill_dir / "references" / "pytest.md").write_text(
        "Use pytest -q for a focused run.\n", encoding="utf-8"
    )
    return skill_dir


def build_agent(tmp_path, outputs, **kwargs):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    workspace = WorkspaceContext.build(tmp_path)
    store = SessionStore(tmp_path / ".bingo" / "sessions")
    return MiniAgent(
        model_client=FakeModelClient(outputs),
        workspace=workspace,
        session_store=store,
        approval_policy="auto",
        **kwargs,
    )


def test_registry_discovers_only_frontmatter_and_reports_invalid_skills(tmp_path):
    write_skill(tmp_path)
    invalid = tmp_path / ".bingo" / "skills" / "broken"
    invalid.mkdir(parents=True)
    (invalid / "SKILL.md").write_text("# no frontmatter\nSECRET BODY", encoding="utf-8")

    registry = SkillRegistry(tmp_path).discover()

    assert list(registry.skills) == ["testing"]
    skill = registry.skills["testing"]
    assert skill.description == "Run project tests"
    assert skill.aliases == ("test",)
    assert skill.resources == ("references/pytest.md",)
    assert "Run the smallest" not in skill.routing_text
    assert registry.fingerprint
    assert registry.diagnostics[0]["code"] == "invalid_frontmatter"


def test_registry_rejects_duplicate_declared_names(tmp_path):
    write_skill(tmp_path, "first")
    second = write_skill(tmp_path, "second")
    text = (
        (second / "SKILL.md")
        .read_text(encoding="utf-8")
        .replace("name: second", "name: first")
    )
    (second / "SKILL.md").write_text(text, encoding="utf-8")

    registry = SkillRegistry(tmp_path).discover()

    assert "first" not in registry.skills
    assert any(item["code"] == "duplicate_name" for item in registry.diagnostics)


def test_router_prioritizes_explicit_names_and_aliases(tmp_path):
    write_skill(tmp_path, "testing")
    write_skill(
        tmp_path,
        "code-review",
        "Review code changes",
        aliases=["review"],
        triggers=["审查", "review"],
    )
    catalog = SkillRegistry(tmp_path).discover()
    router = SkillRouter(catalog)

    explicit = router.route("请用 $testing 检查这次改动")
    alias = router.route("/skill review inspect this patch")

    assert explicit.selected == ("testing",)
    assert explicit.mode == "explicit"
    assert alias.selected == ("code-review",)
    assert alias.mode == "explicit"


def test_router_recognizes_explicit_skill_next_to_chinese_text(tmp_path):
    write_skill(tmp_path, "testing")
    router = SkillRouter(SkillRegistry(tmp_path).discover())

    route = router.route("请用$testing检查改动")

    assert route.selected == ("testing",)
    assert route.mode == "explicit"


def test_router_semantically_matches_metadata_and_falls_back(tmp_path):
    write_skill(tmp_path, "testing")
    catalog = SkillRegistry(tmp_path).discover()
    router = SkillRouter(catalog)

    matched = router.route("请运行 pytest 做一次回归测试")
    fallback = router.route("解释这个项目的整体架构")

    assert matched.selected == ("testing",)
    assert matched.mode == "semantic"
    assert matched.scores["testing"] > 0
    assert fallback.selected == ()
    assert fallback.mode == "fallback"


def test_router_reports_missing_explicit_skill_without_semantic_substitution(tmp_path):
    write_skill(tmp_path, "testing")
    route = SkillRouter(SkillRegistry(tmp_path).discover()).route("请使用 $testng")

    assert route.selected == ()
    assert route.mode == "explicit_missing"
    assert route.missing == ("testng",)
    assert route.suggestions == ("testing",)


def test_loader_reads_body_lazily_and_reuses_same_content_hash(tmp_path):
    write_skill(tmp_path)
    catalog = SkillRegistry(tmp_path).discover()
    session_state = {"loaded": {}, "active": []}
    loader = SkillLoader(catalog, session_state)

    assert session_state["loaded"] == {}
    first = loader.load("testing")
    second = loader.load("testing")

    assert "Run the smallest relevant test first." in first.body
    assert second is first
    assert session_state["loaded"]["testing"]["content_hash"] == first.content_hash
    assert "body" not in session_state["loaded"]["testing"]


def test_loader_reloads_changed_skill_body(tmp_path):
    skill_dir = write_skill(tmp_path)
    catalog = SkillRegistry(tmp_path).discover()
    loader = SkillLoader(catalog, {"loaded": {}, "active": []})
    before = loader.load("testing")
    path = skill_dir / "SKILL.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "smallest relevant", "changed focused"
        ),
        encoding="utf-8",
    )

    after = loader.load("testing")

    assert before.content_hash != after.content_hash
    assert "changed focused" in after.body


def test_loader_reads_only_declared_resources_inside_skill_directory(tmp_path):
    write_skill(tmp_path)
    loader = SkillLoader(
        SkillRegistry(tmp_path).discover(), {"loaded": {}, "active": []}
    )

    result = loader.read_resource("testing", "references/pytest.md", max_chars=4000)

    assert "pytest -q" in result["content"]
    assert result["path"] == "references/pytest.md"
    with pytest.raises(ValueError, match="not declared"):
        loader.read_resource("testing", "../sessions/secret.json")


def test_registry_rejects_skill_directory_symlink_that_escapes_workspace(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-external-skill"
    outside.mkdir(exist_ok=True)
    (outside / "SKILL.md").write_text(
        "---\nname: external\ndescription: outside\n---\n# Outside\nDo not load.\n",
        encoding="utf-8",
    )
    skills_root = tmp_path / ".bingo" / "skills"
    skills_root.mkdir(parents=True)
    try:
        (skills_root / "external").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation requires extra privileges on this platform")

    registry = SkillRegistry(tmp_path).discover()

    assert "external" not in registry.skills
    assert any(item["code"] == "skill_path_escape" for item in registry.diagnostics)


def test_agent_activates_skill_for_one_request_and_injects_whole_body(tmp_path):
    write_skill(tmp_path)
    agent = build_agent(tmp_path, ["<final>tested</final>", "<final>explained</final>"])

    assert agent.ask("请用 $testing 检查") == "tested"
    first_prompt = agent.model_client.prompts[0]
    assert "Active Skill instructions" in first_prompt
    assert "Run the smallest relevant test first." in first_prompt
    assert agent.session["skills"]["loaded"]["testing"]["content_hash"]

    assert agent.ask("解释 README") == "explained"
    assert "Active Skill instructions" not in agent.model_client.prompts[1]
    assert agent.session["skills"]["active"] == []


def test_agent_keeps_active_skill_across_tool_steps(tmp_path):
    write_skill(tmp_path)
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"list_skills","args":{}}</tool>',
            "<final>done</final>",
        ],
    )

    assert agent.ask("$testing run tests") == "done"
    assert all(
        "Run the smallest relevant test first." in prompt
        for prompt in agent.model_client.prompts
    )


def test_skill_tools_list_metadata_and_read_declared_resource(tmp_path):
    write_skill(tmp_path)
    agent = build_agent(tmp_path, [])

    listing = agent.run_tool("list_skills", {})
    denied = agent.run_tool(
        "read_skill_resource",
        {
            "skill_name": "testing",
            "resource_path": "references/pytest.md",
            "max_chars": 4000,
        },
    )
    agent.activate_skills("$testing")
    resource = agent.run_tool(
        "read_skill_resource",
        {
            "skill_name": "testing",
            "resource_path": "references/pytest.md",
            "max_chars": 4000,
        },
    )

    assert "testing: Run project tests" in listing
    assert (
        denied
        == "error: tool read_skill_resource failed: Skill must be active before reading its resources"
    )
    assert "pytest -q" in resource
    assert "sha256=" in resource


def test_skill_allowed_tools_can_only_narrow_base_tools(tmp_path):
    write_skill(tmp_path)
    agent = build_agent(tmp_path, [])
    agent.activate_skills("$testing")

    assert agent.skill_tool_allowed("read_file") is True
    assert agent.skill_tool_allowed("write_file") is False
    assert agent.skill_tool_allowed("list_skills") is True


def test_model_can_activate_registered_skill_then_receive_body_next_step(tmp_path):
    write_skill(tmp_path)
    agent = build_agent(
        tmp_path,
        [
            '<tool>{"name":"activate_skill","args":{"skill_name":"testing"}}</tool>',
            "<final>activated</final>",
        ],
    )

    assert agent.ask("help me improve quality") == "activated"
    assert "Active Skill instructions" not in agent.model_client.prompts[0]
    assert "Available Skill metadata" in agent.model_client.prompts[0]
    assert "testing: Run project tests" in agent.model_client.prompts[0]
    assert "Run the smallest relevant test first." not in agent.model_client.prompts[0]
    assert "Run the smallest relevant test first." in agent.model_client.prompts[1]


def test_unmatched_request_exposes_skill_cards_without_forcing_activation(tmp_path):
    write_skill(tmp_path)
    agent = build_agent(tmp_path, ["<final>general answer</final>"])

    assert agent.ask("解释项目架构") == "general answer"

    prompt = agent.model_client.prompts[0]
    assert "Available Skill metadata" in prompt
    assert "testing: Run project tests" in prompt
    assert "Run the smallest relevant test first." not in prompt
    assert agent.last_skill_route.mode == "fallback"
    assert agent.session["skills"]["active"] == []


def test_active_skill_rejects_tools_outside_its_allowlist(tmp_path):
    write_skill(tmp_path)
    agent = build_agent(tmp_path, [])
    agent.activate_skills("$testing")

    result = agent.run_tool("write_file", {"path": "blocked.txt", "content": "no"})

    assert result == "error: active Skill does not allow tool 'write_file'"
    assert not (tmp_path / "blocked.txt").exists()


def test_context_manager_omits_oversized_skill_as_a_whole_block(tmp_path):
    write_skill(tmp_path)
    agent = build_agent(tmp_path, [])
    agent.activate_skills("$testing")

    prompt, metadata = ContextManager(agent, section_budgets={"skills": 40}).build(
        "run tests"
    )

    assert "Run the smallest relevant test first." not in prompt
    assert metadata["sections"]["skills"]["raw_chars"] > 40
    assert metadata["sections"]["skills"]["rendered_chars"] == 0
