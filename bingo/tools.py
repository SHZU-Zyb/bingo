"""工具定义与执行辅助逻辑。

可以把这个文件看成 agent 的能力白名单：模型能申请哪些动作、这些动作
如何做参数校验，以及最终如何执行，都是在这里定义的。
"""

import shutil
import subprocess
import textwrap
import uuid
from functools import partial

from .workspace import IGNORED_PATH_NAMES, clip

BASE_TOOL_SPECS = {
    "list_files": {
        "schema": {"path": "str='.'"},
        "risky": False,
        "description": "List files in the workspace.",
    },
    "read_file": {
        "schema": {"path": "str", "start": "int=1", "end": "int=200"},
        "risky": False,
        "description": "Read a UTF-8 file by line range.",
    },
    "search": {
        "schema": {"pattern": "str", "path": "str='.'"},
        "risky": False,
        "description": "Search the workspace with rg or a simple fallback.",
    },
    "retrieve_code": {
        "schema": {"query": "str", "path": "str='.'", "mode": "str='auto'", "budget_chars": "int=4000"},
        "risky": False,
        "description": "Find relevant code locations with adaptive symbol/BM25/vector/graph routing. Use read_symbol or read_file to inspect selected source. Index cache is stored under .bingo/retrieval.",
    },
    "read_symbol": {
        "schema": {"symbol_id": "str", "expected_hash": "str=''", "max_chars": "int=4000"},
        "risky": False,
        "description": "Read the exact current source for a symbol_id returned by retrieve_code; rejects stale locations.",
    },
    "list_skills": {
        "schema": {},
        "risky": False,
        "description": "List registered project Skills using metadata only.",
    },
    "read_skill_resource": {
        "schema": {"skill_name": "str", "resource_path": "str", "max_chars": "int=4000"},
        "risky": False,
        "description": "Read a declared reference or template from a registered Skill on demand.",
    },
    "activate_skill": {
        "schema": {"skill_name": "str"},
        "risky": False,
        "description": "Activate one registered Skill selected from metadata; its body appears on the next model step.",
    },
    "parallel_workflow": {
        "schema": {"objective": "str", "branches": "list[object]", "max_parallel": "int=3", "max_steps": "int=3"},
        "risky": False,
        "description": "Run 2-4 independent, cross-module read-only investigations concurrently. Use the parent for work one Agent can finish.",
    },
    "verification_workflow": {
        "schema": {"objective": "str", "command": "str", "timeout": "int=120"},
        "risky": True,
        "description": "Run verification locally, parse results deterministically, and use a diagnostic child only for ambiguous failures.",
    },
    "read_workflow_artifact": {
        "schema": {"workflow_id": "str", "node_id": "str", "artifact": "str", "start_line": "int=1", "end_line": "int=200"},
        "risky": False,
        "description": "Read a bounded line range from a workflow artifact when its compact report is insufficient.",
    },
    "run_shell": {
        "schema": {"command": "str", "timeout": "int=20"},
        "risky": True,
        "description": "Run a shell command in the repo root.",
    },
    "write_file": {
        "schema": {"path": "str", "content": "str"},
        "risky": True,
        "description": "Write a text file.",
    },
    "patch_file": {
        "schema": {"path": "str", "old_text": "str", "new_text": "str"},
        "risky": True,
        "description": "Replace one exact text block in a file.",
    },
}

DELEGATE_TOOL_SPEC = {
    "schema": {"task": "str", "max_steps": "int=3"},
    "risky": False,
    "description": "Ask a bounded read-only child agent to investigate.",
}

TOOL_EXAMPLES = {
    "retrieve_code": '<tool>{"name":"retrieve_code","args":{"query":"where is session recovery implemented","mode":"auto"}}</tool>',
    "read_symbol": '<tool>{"name":"read_symbol","args":{"symbol_id":"<id from retrieve_code>","expected_hash":"<sha256>"}}</tool>',
    "list_skills": '<tool>{"name":"list_skills","args":{}}</tool>',
    "read_skill_resource": '<tool>{"name":"read_skill_resource","args":{"skill_name":"testing","resource_path":"references/pytest.md","max_chars":4000}}</tool>',
    "activate_skill": '<tool>{"name":"activate_skill","args":{"skill_name":"testing"}}</tool>',
    "parallel_workflow": '<tool>{"name":"parallel_workflow","args":{"objective":"inspect auth impact","branches":[{"id":"api","task":"inspect API auth","scope":["bingo/api"]},{"id":"tests","task":"inspect auth tests","scope":["tests"]}]}}</tool>',
    "verification_workflow": '<tool>{"name":"verification_workflow","args":{"objective":"verify the change","command":"python -m pytest -q","timeout":120}}</tool>',
    "read_workflow_artifact": '<tool>{"name":"read_workflow_artifact","args":{"workflow_id":"wf_123","node_id":"runner","artifact":"stdout.log","start_line":1,"end_line":80}}</tool>',
    "list_files": '<tool>{"name":"list_files","args":{"path":"."}}</tool>',
    "read_file": '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":80}}</tool>',
    "search": '<tool>{"name":"search","args":{"pattern":"binary_search","path":"."}}</tool>',
    "run_shell": '<tool>{"name":"run_shell","args":{"command":"uv run --with pytest python -m pytest -q","timeout":20}}</tool>',
    "write_file": '<tool name="write_file" path="binary_search.py"><content>def binary_search(nums, target):\n    return -1\n</content></tool>',
    "patch_file": '<tool name="patch_file" path="binary_search.py"><old_text>return -1</old_text><new_text>return mid</new_text></tool>',
    "delegate": '<tool>{"name":"delegate","args":{"task":"inspect README.md","max_steps":3}}</tool>',
}


def build_tool_registry(agent):
    # 工具不是动态发现的，而是显式注册的。
    # 这样模型看到的是一个有边界、可审计的动作集合。
    tools = {
        name: {**spec, "run": partial(_TOOL_RUNNERS[name], agent)}
        for name, spec in BASE_TOOL_SPECS.items()
    }
    # 子 agent 是刻意做成受限能力的：一旦深度耗尽，
    # 就连 delegate 这个工具都不再暴露给模型。
    if agent.depth < agent.max_depth:
        tools["delegate"] = {**DELEGATE_TOOL_SPEC, "run": partial(tool_delegate, agent)}
    if hasattr(agent, "feature_enabled") and not agent.feature_enabled("workflows"):
        for name in ("parallel_workflow", "verification_workflow", "read_workflow_artifact"):
            tools.pop(name, None)
    allowed_tools = getattr(agent, "allowed_tools", None)
    if allowed_tools is not None:
        tools = {name: tool for name, tool in tools.items() if name in allowed_tools}
    return tools


def tool_example(name):
    return TOOL_EXAMPLES.get(name, "")


def validate_tool(agent, name, args):
    args = args or {}

    if name == "retrieve_code":
        query = args.get("query", "")
        if not isinstance(query, str) or not query.strip() or len(query) > 8000:
            raise ValueError("query must contain 1 to 8000 characters")
        agent.path(args.get("path", "."))
        if args.get("mode", "auto") not in {"auto", "direct", "symbol", "keyword", "hybrid", "vector"}:
            raise ValueError("invalid retrieval mode")
        if not 1 <= int(args.get("budget_chars", 4000)) <= 4000:
            raise ValueError("tool budget_chars must be in [1,4000]")
        return

    if name == "read_symbol":
        symbol_id = args.get("symbol_id", "")
        if not isinstance(symbol_id, str) or not symbol_id or len(symbol_id) > 128:
            raise ValueError("invalid symbol_id")
        expected_hash = args.get("expected_hash", "")
        if not isinstance(expected_hash, str) or len(expected_hash) > 128:
            raise ValueError("invalid expected_hash")
        if not 256 <= int(args.get("max_chars", 4000)) <= 4000:
            raise ValueError("tool max_chars must be in [256,4000]")
        return

    if name == "list_skills":
        if args:
            raise ValueError("list_skills does not accept arguments")
        return

    if name == "read_skill_resource":
        skill_name = args.get("skill_name", "")
        resource_path = args.get("resource_path", "")
        if not isinstance(skill_name, str) or not skill_name.strip() or len(skill_name) > 64:
            raise ValueError("invalid skill_name")
        if not isinstance(resource_path, str) or not resource_path.strip() or len(resource_path) > 256:
            raise ValueError("invalid resource_path")
        if not 1 <= int(args.get("max_chars", 4000)) <= 4000:
            raise ValueError("max_chars must be in [1,4000]")
        return

    if name == "activate_skill":
        skill_name = args.get("skill_name", "")
        if not isinstance(skill_name, str) or not skill_name.strip() or len(skill_name) > 64:
            raise ValueError("invalid skill_name")
        return

    if name == "parallel_workflow":
        objective = args.get("objective", "")
        branches = args.get("branches")
        if not isinstance(objective, str) or not objective.strip() or len(objective) > 2000:
            raise ValueError("objective must contain 1 to 2000 characters")
        if not isinstance(branches, list) or not 2 <= len(branches) <= 4:
            raise ValueError("parallel workflow requires 2 to 4 branches")
        if not 2 <= int(args.get("max_parallel", 3)) <= 4:
            raise ValueError("max_parallel must be in [2,4]")
        if not 1 <= int(args.get("max_steps", 3)) <= 4:
            raise ValueError("max_steps must be in [1,4]")
        for branch in branches:
            if not isinstance(branch, dict):
                raise TypeError("each branch must be an object")
            if not str(branch.get("id", "")).strip() or not str(branch.get("task", "")).strip():
                raise ValueError("each branch requires id and task")
            scope = branch.get("scope")
            if not isinstance(scope, list) or not scope:
                raise ValueError("each branch requires a non-empty scope list")
            for path in scope:
                agent.path(path)
        return

    if name == "verification_workflow":
        objective = args.get("objective", "")
        command = args.get("command", "")
        if not isinstance(objective, str) or not objective.strip() or len(objective) > 2000:
            raise ValueError("objective must contain 1 to 2000 characters")
        if not isinstance(command, str) or not command.strip() or len(command) > 8000:
            raise ValueError("command must contain 1 to 8000 characters")
        if not 1 <= int(args.get("timeout", 120)) <= 300:
            raise ValueError("timeout must be in [1,300]")
        return

    if name == "read_workflow_artifact":
        for key in ("workflow_id", "node_id", "artifact"):
            value = args.get(key, "")
            if not isinstance(value, str) or not value.strip() or len(value) > 128:
                raise ValueError(f"invalid {key}")
        start_line = int(args.get("start_line", 1))
        end_line = int(args.get("end_line", 200))
        if start_line < 1 or end_line < start_line or end_line - start_line >= 500:
            raise ValueError("artifact line range must contain at most 500 lines")
        return

    if name == "list_files":
        path = agent.path(args.get("path", "."))
        if not path.is_dir():
            raise ValueError("path is not a directory")
        return

    if name == "read_file":
        path = agent.path(args["path"])
        if not path.is_file():
            raise ValueError("path is not a file")
        start = int(args.get("start", 1))
        end = int(args.get("end", 200))
        if start < 1 or end < start:
            raise ValueError("invalid line range")
        return

    if name == "search":
        pattern = str(args.get("pattern", "")).strip()
        if not pattern:
            raise ValueError("pattern must not be empty")
        agent.path(args.get("path", "."))
        return

    if name == "run_shell":
        command = str(args.get("command", "")).strip()
        if not command:
            raise ValueError("command must not be empty")
        timeout = int(args.get("timeout", 20))
        if timeout < 1 or timeout > 120:
            raise ValueError("timeout must be in [1, 120]")
        return

    if name == "write_file":
        path = agent.path(args["path"])
        if path.exists() and path.is_dir():
            raise ValueError("path is a directory")
        if "content" not in args:
            raise ValueError("missing content")
        return

    if name == "patch_file":
        # patch_file 故意做得很严格：old_text 必须精确命中且只能出现一次，
        # 这样修改行为才是确定的，失败原因也更容易解释。
        path = agent.path(args["path"])
        if not path.is_file():
            raise ValueError("path is not a file")
        old_text = str(args.get("old_text", ""))
        if not old_text:
            raise ValueError("old_text must not be empty")
        if "new_text" not in args:
            raise ValueError("missing new_text")
        text = path.read_text(encoding="utf-8")
        count = text.count(old_text)
        if count != 1:
            raise ValueError(f"old_text must occur exactly once, found {count}")
        return

    if name == "delegate":
        task = str(args.get("task", "")).strip()
        if not task:
            raise ValueError("task must not be empty")
        return


def tool_list_files(agent, args):
    path = agent.path(args.get("path", "."))
    if not path.is_dir():
        raise ValueError("path is not a directory")
    entries = [
        item for item in sorted(path.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        if item.name not in IGNORED_PATH_NAMES
    ]
    lines = []
    for entry in entries[:200]:
        kind = "[D]" if entry.is_dir() else "[F]"
        lines.append(f"{kind} {entry.relative_to(agent.root)}")
    return "\n".join(lines) or "(empty)"


def tool_read_file(agent, args):
    path = agent.path(args["path"])
    if not path.is_file():
        raise ValueError("path is not a file")
    start = int(args.get("start", 1))
    end = int(args.get("end", 200))
    if start < 1 or end < start:
        raise ValueError("invalid line range")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    body = "\n".join(f"{number:>4}: {line}" for number, line in enumerate(lines[start - 1:end], start=start))
    return f"# {path.relative_to(agent.root)}\n{body}"


def tool_search(agent, args):
    pattern = str(args.get("pattern", "")).strip()
    if not pattern:
        raise ValueError("pattern must not be empty")
    path = agent.path(args.get("path", "."))

    if shutil.which("rg"):
        # 优先用 rg，因为搜索会非常频繁，搜索延迟会直接影响 agent 控制循环。
        result = subprocess.run(
            ["rg", "-n", "--smart-case", "--max-count", "200", pattern, str(path)],
            cwd=agent.root,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() or result.stderr.strip() or "(no matches)"

    matches = []
    files = [path] if path.is_file() else [
        item for item in path.rglob("*")
        if item.is_file() and not any(part in IGNORED_PATH_NAMES for part in item.relative_to(agent.root).parts)
    ]
    for file_path in files:
        for number, line in enumerate(file_path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if pattern.lower() in line.lower():
                matches.append(f"{file_path.relative_to(agent.root)}:{number}:{line}")
                if len(matches) >= 200:
                    return "\n".join(matches)
    return "\n".join(matches) or "(no matches)"


def tool_retrieve_code(agent, args):
    budget = min(int(args.get("budget_chars", 4000)), 4000)
    result = agent.get_retrieval_engine().search(
        args["query"], path=args.get("path", "."), mode=args.get("mode", "auto"),
        budget_chars=budget, budget_tokens=budget)
    agent.last_retrieval = result
    return result["text"] or f"No code evidence within budget. strategy={result['strategy']}; fallback={result['fallback_reason'] or 'none'}"[:budget]


def tool_read_symbol(agent, args):
    item = agent.get_retrieval_engine().read_symbol(
        args["symbol_id"], expected_hash=args.get("expected_hash") or None,
        max_chars=min(int(args.get("max_chars", 4000)), 4000))
    suffix = "\n[truncated; request the remaining file range with read_file]" if item["truncated"] else ""
    return (f"[{item['path']}:{item['start_line']}-{item['returned_end_line']}] "
            f"{item['qualified_name']} sha256={item['content_hash']}\n{item['content']}{suffix}")


def tool_list_skills(agent, args):
    agent.refresh_skill_registry()
    lines = [
        f"- {metadata.name}: {metadata.description} (version={metadata.version})"
        for metadata in agent.skill_registry.skills.values()
    ]
    if agent.skill_registry.diagnostics:
        lines.append(f"diagnostics: {len(agent.skill_registry.diagnostics)} invalid Skill item(s)")
    return "Registered Skills:\n" + ("\n".join(lines) if lines else "- none")


def tool_read_skill_resource(agent, args):
    active_names = {skill.name for skill in getattr(agent, "_active_skills", [])}
    requested_name = str(args["skill_name"]).strip().casefold()
    active_aliases = set(active_names)
    for name in active_names:
        metadata = agent.skill_registry.skills.get(name)
        if metadata is not None:
            active_aliases.update(alias.casefold() for alias in metadata.aliases)
    if requested_name not in active_aliases:
        raise ValueError("Skill must be active before reading its resources")
    item = agent.skill_loader.read_resource(
        args["skill_name"],
        args["resource_path"],
        max_chars=int(args.get("max_chars", 4000)),
    )
    suffix = "\n[truncated; request a smaller resource or split it]" if item["truncated"] else ""
    return (
        f"[{item['skill_name']}:{item['path']}] sha256={item['content_hash']}\n"
        f"{item['content']}{suffix}"
    )


def tool_activate_skill(agent, args):
    loaded = agent.activate_skill_by_name(args["skill_name"])
    return (
        f"activated Skill {loaded.name} version={loaded.version} sha256={loaded.content_hash}; "
        "its instructions will be available on the next model step"
    )


def tool_parallel_workflow(agent, args):
    result = agent.run_parallel_workflow(
        args["objective"],
        args["branches"],
        max_parallel=int(args.get("max_parallel", 3)),
        max_steps=int(args.get("max_steps", 3)),
    )
    return agent.compact_workflow_result(result)


def tool_verification_workflow(agent, args):
    result = agent.run_verification_workflow(
        args["objective"],
        args["command"],
        timeout=int(args.get("timeout", 120)),
    )
    return agent.compact_workflow_result(result)


def tool_read_workflow_artifact(agent, args):
    item = agent.read_workflow_artifact(
        args["workflow_id"],
        args["node_id"],
        args["artifact"],
        start_line=int(args.get("start_line", 1)),
        end_line=int(args.get("end_line", 200)),
    )
    suffix = "\n[truncated; request a smaller line range]" if item["truncated"] else ""
    return f"[{item['workflow_id']}:{item['artifact_ref']}]\n{item['content']}{suffix}"


def tool_run_shell(agent, args):
    command = str(args.get("command", "")).strip()
    if not command:
        raise ValueError("command must not be empty")
    timeout = int(args.get("timeout", 20))
    if timeout < 1 or timeout > 120:
        raise ValueError("timeout must be in [1, 120]")
    result = subprocess.run(
        command,
        cwd=agent.root,
        shell=True,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        # 这里传入的是过滤后的环境变量，而不是直接继承整个父 shell 环境，
        # 目的是减少敏感信息被意外带进命令执行环境的风险。
        env=agent.shell_env(),
    )
    return textwrap.dedent(
        f"""\
        exit_code: {result.returncode}
        stdout:
        {result.stdout.strip() or "(empty)"}
        stderr:
        {result.stderr.strip() or "(empty)"}
        """
    ).strip()


def tool_write_file(agent, args):
    path = agent.path(args["path"])
    content = str(args["content"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"wrote {path.relative_to(agent.root)} ({len(content)} chars)"


def tool_patch_file(agent, args):
    path = agent.path(args["path"])
    if not path.is_file():
        raise ValueError("path is not a file")
    old_text = str(args.get("old_text", ""))
    if not old_text:
        raise ValueError("old_text must not be empty")
    if "new_text" not in args:
        raise ValueError("missing new_text")
    text = path.read_text(encoding="utf-8")
    count = text.count(old_text)
    if count != 1:
        raise ValueError(f"old_text must occur exactly once, found {count}")
    path.write_text(text.replace(old_text, str(args["new_text"]), 1), encoding="utf-8")
    return f"patched {path.relative_to(agent.root)}"


def tool_delegate(agent, args):
    if agent.depth >= agent.max_depth:
        raise ValueError("delegate depth exceeded")
    task = str(args.get("task", "")).strip()
    if not task:
        raise ValueError("task must not be empty")

    from .run_store import RunStore
    from .runtime import Bingo, SessionStore

    child_root = agent.root / ".bingo" / "children" / uuid.uuid4().hex[:12]
    child_tools = {
        "list_files", "read_file", "search", "list_skills", "activate_skill",
        "read_skill_resource", "read_workflow_artifact",
    }

    child = Bingo(
        model_client=agent._child_model_client("legacy-investigator"),
        workspace=agent.workspace,
        session_store=SessionStore(child_root / "sessions"),
        run_store=RunStore(child_root / "runs"),
        approval_policy="never",
        max_steps=int(args.get("max_steps", 3)),
        max_new_tokens=agent.max_new_tokens,
        depth=agent.depth + 1,
        max_depth=agent.max_depth,
        read_only=True,
        secret_env_names=agent.secret_env_names,
        shell_env_allowlist=agent.shell_env_allowlist,
        auto_retrieve=False,
        allowed_tools=child_tools,
        child_client_factory=agent.child_client_factory,
        workflow_store_root=agent.workflow_store().root,
    )
    # 委派的目标是“调查”，不是“放权执行”。
    # 子 agent 以只读方式运行、步数更少，最后只把结论文本返回给父 agent。
    child.session["memory"]["task"] = task

    parent_history = agent.context_manager._render_history_section(300).rendered
    child.session["memory"]["notes"] = [clip(parent_history, 300)]
    return "delegate_result:\n" + child.ask(task)


_TOOL_RUNNERS = {
    "retrieve_code": tool_retrieve_code,
    "read_symbol": tool_read_symbol,
    "list_skills": tool_list_skills,
    "read_skill_resource": tool_read_skill_resource,
    "activate_skill": tool_activate_skill,
    "parallel_workflow": tool_parallel_workflow,
    "verification_workflow": tool_verification_workflow,
    "read_workflow_artifact": tool_read_workflow_artifact,
    "list_files": tool_list_files,
    "read_file": tool_read_file,
    "search": tool_search,
    "run_shell": tool_run_shell,
    "write_file": tool_write_file,
    "patch_file": tool_patch_file,
}
