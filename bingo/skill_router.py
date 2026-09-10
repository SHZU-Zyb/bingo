"""Deterministic explicit and metadata-semantic Skill routing."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

EXPLICIT_PATTERN = re.compile(
    r"(?<![a-z0-9_-])(?:\$|/skill\s+)([a-z0-9][a-z0-9-]{0,63})", re.IGNORECASE
)
WORD_PATTERN = re.compile(r"[a-z0-9_-]+|[\u4e00-\u9fff]+", re.IGNORECASE)


@dataclass(frozen=True)
class SkillRoute:
    selected: tuple[str, ...] = ()
    mode: str = "fallback"
    reason: str = "no matching skill"
    scores: dict[str, float] = field(default_factory=dict)
    missing: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = ()


def _normalized(text):
    return str(text).casefold().strip()


def _terms(text):
    normalized = _normalized(text)
    terms = set(WORD_PATTERN.findall(normalized))
    # Chinese requests often have no spaces.  Character bigrams make metadata
    # matching useful without loading a separate tokenizer.
    chinese = "".join(char for char in normalized if "\u4e00" <= char <= "\u9fff")
    terms.update(
        chinese[index : index + 2] for index in range(max(0, len(chinese) - 1))
    )
    return {term for term in terms if term}


class SkillRouter:
    def __init__(self, registry, *, threshold=0.35, max_active=2):
        self.registry = registry
        self.threshold = float(threshold)
        self.max_active = int(max_active)

    def route(self, user_message):
        text = _normalized(user_message)
        explicit_names = [
            match.group(1).lower() for match in EXPLICIT_PATTERN.finditer(text)
        ]
        if explicit_names:
            return self._explicit(explicit_names)

        scores = {
            name: self._score(text, metadata)
            for name, metadata in self.registry.skills.items()
        }
        ranked = sorted(
            (
                (score, metadata.priority, name)
                for name, metadata in self.registry.skills.items()
                if (score := scores[name]) >= self.threshold
            ),
            reverse=True,
        )
        if not ranked:
            return SkillRoute(scores=scores)
        best_score, _, best_name = ranked[0]
        return SkillRoute(
            selected=(best_name,),
            mode="semantic",
            reason=f"metadata match score={best_score:.3f}",
            scores=scores,
        )

    def _explicit(self, requested):
        alias_map = {}
        for name, metadata in self.registry.skills.items():
            alias_map[name.casefold()] = name
            alias_map.update({alias.casefold(): name for alias in metadata.aliases})
        selected, missing = [], []
        for requested_name in requested[: self.max_active]:
            resolved = alias_map.get(requested_name.casefold())
            target = selected if resolved else missing
            value = resolved or requested_name
            if value not in target:
                target.append(value)
        suggestions = []
        choices = sorted(alias_map)
        for name in missing:
            for match in difflib.get_close_matches(name, choices, n=1, cutoff=0.5):
                resolved = alias_map[match]
                if resolved not in suggestions:
                    suggestions.append(resolved)
        if missing:
            return SkillRoute(
                mode="explicit_missing",
                reason="one or more explicitly requested skills were not found",
                missing=tuple(missing),
                suggestions=tuple(suggestions),
            )
        return SkillRoute(
            selected=tuple(selected),
            mode="explicit",
            reason="user explicitly selected skill",
            scores={name: 1.0 for name in selected},
        )

    @staticmethod
    def _score(query, metadata):
        best = 0.0
        for alias in metadata.aliases:
            alias = _normalized(alias)
            if alias and re.search(
                rf"(?<![a-z0-9_-]){re.escape(alias)}(?![a-z0-9_-])", query
            ):
                best = max(best, 0.9)
        matched_triggers = [
            trigger for trigger in metadata.triggers if _normalized(trigger) in query
        ]
        if matched_triggers:
            best = max(best, min(1.0, 0.55 + 0.15 * len(matched_triggers)))
        query_terms = _terms(query)
        routing_terms = _terms(metadata.routing_text)
        if query_terms and routing_terms:
            overlap = len(query_terms & routing_terms) / max(
                1, min(len(query_terms), len(routing_terms))
            )
            best = max(best, overlap * 0.7)
        return round(best, 6)
