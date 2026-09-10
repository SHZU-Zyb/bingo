# Workflow routing reference

## Decision order

1. Can the parent Agent finish the work directly? Keep it in the parent.
2. Is the work deterministic? Use a local tool or algorithm.
3. Are there 2-4 independent investigation scopes with no data dependency? Use `parallel_workflow`.
4. Did local verification fail in a way that deterministic parsing cannot explain compactly? Let `verification_workflow` invoke one diagnostician.
5. Otherwise do not create a child Agent.

## Good parallel branches

- API compatibility and storage migration analysis in separate directories.
- Backend behavior, CLI behavior, and test coverage inspection when each scope can produce its own evidence.
- Independent security boundary checks in unrelated modules.

## Bad parallel branches

- Read implementation, then edit it, then run tests. Each step depends on the previous result.
- Two branches that both cover the same files.
- One small investigation that the parent can finish with one search and one read.
- Test execution or log counting. Local algorithms handle these more cheaply and exactly.

## Diagnostic gate

No child is needed for a passing command or one clear pytest failure with a useful message. Escalation is allowed for multiple failures, failure cards without details, failures spanning modules, or nonstandard output that produced no structured failure card.

The diagnostician receives parsed facts first. Full stdout and stderr stay in artifacts and can be read in bounded line ranges only when needed.
