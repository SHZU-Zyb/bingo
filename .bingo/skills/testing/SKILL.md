---
name: testing
description: Design, run, diagnose, and report focused project tests and regression checks
version: '1.0'
aliases:
  - test
  - pytest
triggers:
  - 测试
  - 单元测试
  - 回归验证
  - test failure
  - coverage
priority: 60
allowed_tools:
  - list_files
  - read_file
  - search
  - retrieve_code
  - read_symbol
  - run_shell
  - verification_workflow
  - parallel_workflow
  - read_workflow_artifact
  - write_file
  - patch_file
resources:
  references:
    - references/pytest.md
  templates:
    - templates/test-report.md
---
# Testing Skill

1. Identify the project's actual test runner and the smallest relevant test scope.
2. When fixing a failure, reproduce it before changing production code.
3. Read the implementation and existing tests before editing.
4. Run the focused test after each behavior change, then run the relevant regression suite.
5. Report the exact command, pass/fail/skip counts, and any limitation that prevented a broader run.

Use `references/pytest.md` only for pytest-specific command guidance. Use
`templates/test-report.md` only when a structured validation report is useful.
