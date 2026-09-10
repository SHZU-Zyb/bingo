---
name: multi-agent-workflow
description: Route complex repository work between the parent Agent, deterministic local tools, and bounded parallel or diagnostic child Agents
version: '1.0'
aliases:
  - multi-agent
  - workflow
triggers:
  - 多 Agent
  - 多智能体
  - 并行分析
  - 跨模块分析
  - 复杂诊断
  - parallel investigation
priority: 65
allowed_tools:
  - list_files
  - read_file
  - search
  - retrieve_code
  - read_symbol
  - list_skills
  - activate_skill
  - read_skill_resource
  - parallel_workflow
  - verification_workflow
  - read_workflow_artifact
  - run_shell
  - write_file
  - patch_file
resources:
  references:
    - references/routing.md
---
# Adaptive Multi-Agent Workflow Skill

1. Keep the task in the parent Agent when one Agent can complete it within the current context.
2. Use deterministic local tools for file search, source editing, command execution, test counts, failure extraction, and artifact storage.
3. Use `parallel_workflow` only when there are at least two independent cross-module investigation scopes. Branches must not depend on one another.
4. Keep all source writes in the parent Agent. Parallel children investigate and return compact evidence; they never edit or execute commands.
5. Use `verification_workflow` instead of delegating ordinary test execution. Its local parser returns clear failures directly and invokes a diagnostic child only for ambiguous, multiple, cross-module, or unstructured failures.
6. Prefer compact reports and source references. Call `read_workflow_artifact` for a bounded excerpt only when a report lacks a necessary fact.
7. If the gates are not satisfied, continue the generic parent Agent flow without selecting a child.

Read `references/routing.md` only when branch design or diagnostic escalation needs more detail.
