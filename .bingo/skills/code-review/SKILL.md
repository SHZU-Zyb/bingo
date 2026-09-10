---
name: code-review
description: Review code changes for correctness, regressions, security boundaries, and missing tests
version: '1.0'
aliases:
  - review
  - inspect-changes
triggers:
  - 代码审查
  - code review
  - review this patch
  - regression risk
priority: 55
allowed_tools:
  - list_files
  - read_file
  - search
  - retrieve_code
  - read_symbol
  - parallel_workflow
  - read_workflow_artifact
resources:
  checklists:
    - checklists/default.md
---
# Code Review Skill

1. Read the changed behavior and its callers before drawing conclusions.
2. Prioritize correctness, data loss, security boundaries, and backward compatibility.
3. Tie every finding to a concrete trigger and observable impact.
4. Avoid style-only findings unless they hide a correctness problem.
5. If no actionable defect remains, say so and identify the validation coverage reviewed.

Read `checklists/default.md` only when a full review checklist is needed.
