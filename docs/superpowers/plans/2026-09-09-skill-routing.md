# Skill Routing and Lazy Loading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add project-local Skill discovery, explicit and semantic routing, lazy body/resource loading, session caching, and budgeted prompt integration without changing the generic Agent fallback.

**Architecture:** `.bingo/skills/*/SKILL.md` files expose bounded YAML-style frontmatter to the registry. A deterministic router gives explicit names and aliases priority, then scores metadata terms; the loader reads a selected body and declared resources only after routing, while the context manager injects active Skill instructions as a whole block. Session state separates cached `loaded` entries from per-request `active` entries.

**Tech Stack:** Python standard library, dataclasses, pytest, existing Bingo runtime/tool/context interfaces.

---

### Task 1: Skill metadata discovery

**Files:**
- Create: `bingo/skill_registry.py`
- Test: `tests/test_skills.py`

- [x] Write failing tests for bounded frontmatter discovery, invalid metadata isolation, duplicate names, and registry fingerprints.
- [x] Run `python -m pytest tests/test_skills.py -q` and confirm imports fail because the registry does not exist.
- [x] Implement immutable metadata records and safe `.bingo/skills/*/SKILL.md` discovery without reading Skill bodies.
- [x] Run `python -m pytest tests/test_skills.py -q` and confirm registry tests pass.

### Task 2: Explicit and semantic routing

**Files:**
- Create: `bingo/skill_router.py`
- Modify: `tests/test_skills.py`

- [x] Write failing tests for `$name`, `/skill name`, aliases, trigger/description scoring, missing explicit names, score thresholds, and generic fallback.
- [x] Run the focused router tests and confirm the missing implementation causes the expected failures.
- [x] Implement deterministic routing with priority `explicit > alias > metadata score > fallback`, a maximum of two explicit Skills, and explainable route records.
- [x] Run the focused tests and confirm they pass.

### Task 3: Lazy body and resource loader

**Files:**
- Create: `bingo/skill_loader.py`
- Modify: `tests/test_skills.py`

- [x] Write failing tests for lazy body loading, content-hash reuse, reload after changes, declared-resource reads, extension/size limits, and traversal rejection.
- [x] Run the focused loader tests and confirm they fail for the missing implementation.
- [x] Implement whole-body loading, session cache records, resource manifests, and resolved-path containment checks.
- [x] Run the focused tests and confirm they pass.

### Task 4: Runtime, tools, and prompt integration

**Files:**
- Modify: `bingo/runtime.py`
- Modify: `bingo/tools.py`
- Modify: `bingo/context_manager.py`
- Modify: `bingo/__init__.py`
- Modify: `tests/test_skills.py`
- Modify: `tests/test_context_manager.py`

- [x] Write failing integration tests proving a Skill activates before the first model call, remains active across tool steps, clears on the next request, exposes `list_skills` and `read_skill_resource`, and cannot expand tool permissions.
- [x] Run the focused integration tests and confirm the expected failures.
- [x] Initialize the registry/router/loader in `Bingo`, persist only hashes and references in session state, route once per request, expose safe tools, and inject a whole `skills` section after the prefix.
- [x] Add Skill selection and context-budget metadata to trace/report output.
- [x] Run the focused integration and context tests and confirm they pass.

### Task 5: Documentation and full verification

**Files:**
- Create: `docs/skills.md`
- Modify: `README.md`

- [x] Document the directory convention, frontmatter schema, routing priority, session lifecycle, resource API, budget limits, fallbacks, and security boundaries.
- [x] Run `python -m pytest tests/test_skills.py tests/test_context_manager.py tests/test_bingo.py tests/test_safety_invariants.py -q`.
- [x] Run `python -m pytest -q` and record the fresh pass/skip counts in `docs/skills.md`.
- [x] Review all changed files against the approved requirements and remove placeholders or stale statements.

