"""Prompt 组装与上下文预算控制。

这个模块负责路由并装配 prefix、工作状态、召回记忆、代码候选、
新鲜源码证据、历史以及当前用户请求。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .context_router import route_context

DEFAULT_TOTAL_BUDGET = 48000
DEFAULT_SECTION_BUDGETS = {
    "prefix": 6000,
    "skill_catalog": 2000,
    "skills": 8000,
    "memory": 1800,
    "relevant_memory": 1200,
    "history": 7000,
    "run_control": 500,
}
DEFAULT_SECTION_FLOORS = {
    "prefix": 1200,
    "skill_catalog": 0,
    "skills": 0,
    "memory": 400,
    "relevant_memory": 300,
    "history": 1500,
    "run_control": 0,
}
# 当 prompt 超预算时，会优先压缩这些 section。
DEFAULT_REDUCTION_ORDER = (
    "run_control",
    "skill_catalog",
    "relevant_memory",
    "retrieval",
    "history",
    "source_evidence",
    "memory",
    "prefix",
)
SECTION_ORDER = (
    "prefix",
    "skill_catalog",
    "skills",
    "memory",
    "relevant_memory",
    "history",
    "run_control",
    "current_request",
)
CURRENT_REQUEST_SECTION = "current_request"
RELEVANT_MEMORY_LIMIT = 3


def _tail_clip(text, limit):
    text = str(text)
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


@dataclass
class SectionRender:
    raw: str
    budget: int
    rendered: str
    details: dict | None = None

    @property
    def raw_chars(self):
        return len(self.raw)

    @property
    def rendered_chars(self):
        return len(self.rendered)


class ContextManager:
    def __init__(
        self,
        agent,
        total_budget=DEFAULT_TOTAL_BUDGET,
        section_budgets=None,
        section_floors=None,
        reduction_order=None,
    ):
        self.agent = agent
        self.section_order = SECTION_ORDER
        self._retrieval_result = {}
        self._context_route = {}
        self._source_evidence = []
        self._stale_evidence_invalidations = []
        if getattr(agent, "auto_retrieve", False):
            history_index = SECTION_ORDER.index("history")
            self.section_order = (
                *SECTION_ORDER[:history_index],
                "retrieval",
                "source_evidence",
                *SECTION_ORDER[history_index:],
            )
        self.total_budget = int(total_budget)
        self.section_budgets = dict(DEFAULT_SECTION_BUDGETS)
        if section_budgets:
            self.section_budgets.update({str(key): int(value) for key, value in section_budgets.items()})
        if getattr(agent, "auto_retrieve", False):
            self.section_budgets["retrieval"] = 2400
            self.section_budgets["source_evidence"] = 20000
        self._section_floor_overrides = {"run_control": 0}
        self._section_floor_overrides.update(
            {str(key): int(value) for key, value in (section_floors or {}).items()}
        )
        self.section_floors = self._compute_section_floors()
        self.reduction_order = tuple(reduction_order or DEFAULT_REDUCTION_ORDER)
        if getattr(agent, "auto_retrieve", False):
            self._section_floor_overrides["retrieval"] = 0
            self._section_floor_overrides["source_evidence"] = 0

    def build(self, user_message):
        """按预算组装一轮完整 prompt。

        为什么存在：
        仅靠用户这一轮输入，模型并不知道当前仓库状态、会话里已经读过什么、
        哪些旧信息还值得继续参考。这个函数先选择需要的信息源，再把稳定基线、
        工作状态、召回记忆、代码候选、源码证据、历史和当前请求装入 prompt。

        输入 / 输出：
        - 输入：`user_message`，也就是用户当前这一轮的新请求。
        - 输出：`(prompt, metadata)`。
          `prompt` 是最终发送给模型的文本；
          `metadata` 记录了每个 section 的原始长度、裁剪后的长度、是否触发了
          预算收缩等信息，后续会进入 trace/report，便于解释这轮 prompt
          是怎么被拼出来的。

        在 agent 链路里的位置：
        它位于 `Bingo.ask()` 的每轮模型调用之前，是“真正发请求给模型”
        的最后一道组装工序。`WorkspaceContext` 提供稳定前缀，`LayeredMemory`
        提供工作记忆，这个函数则把它们和当前请求合成一份可控大小的 prompt。
        """
        user_message = str(user_message)
        self.section_floors = self._compute_section_floors()
        memory_enabled = True
        relevant_memory_enabled = True
        context_reduction_enabled = True
        if hasattr(self.agent, "feature_enabled"):
            memory_enabled = self.agent.feature_enabled("memory")
            relevant_memory_enabled = self.agent.feature_enabled("relevant_memory")
            context_reduction_enabled = self.agent.feature_enabled("context_reduction")
        section_texts = {
            "prefix": str(getattr(self.agent, "prefix", "")),
            "skill_catalog": str(self.agent.render_skill_catalog())
            if hasattr(self.agent, "render_skill_catalog")
            else "",
            "skills": str(self.agent.render_active_skills()) if hasattr(self.agent, "render_active_skills") else "",
            "memory": (
                "Working state:\n- disabled"
                if not memory_enabled
                else str(self.agent.memory_text())
            ),
            "history": "",
            "run_control": str(self.agent.render_run_control())
            if hasattr(self.agent, "render_run_control")
            else "",
            CURRENT_REQUEST_SECTION: f"Current user request:\n{user_message}",
        }
        checkpoint_text = ""
        if hasattr(self.agent, "render_checkpoint_text"):
            checkpoint_text = str(self.agent.render_checkpoint_text() or "").strip()
        if checkpoint_text:
            # Checkpoint state is the volatile resume contract. Keep it at the front so
            # a growing tool catalog cannot clip the goal/blocker from the prefix tail.
            section_texts["prefix"] = checkpoint_text + "\n\n" + section_texts["prefix"]
        selected_notes = []
        if memory_enabled and relevant_memory_enabled and hasattr(self.agent, "memory") and hasattr(self.agent.memory, "retrieval_candidates"):
            selected_notes = self.agent.memory.retrieval_candidates(user_message, limit=RELEVANT_MEMORY_LIMIT)

        self._context_route = route_context(
            user_message,
            auto_retrieve=getattr(self.agent, "auto_retrieve", False),
            memory_enabled=memory_enabled,
            relevant_memory_enabled=relevant_memory_enabled,
            memory_candidate_count=len(selected_notes),
        )
        if not self._context_route["use_memory_recall"]:
            selected_notes = []

        self._retrieval_result = {}
        self._source_evidence = []
        self._stale_evidence_invalidations = list(
            getattr(self.agent, "_last_stale_evidence_invalidations", [])
        )
        if "retrieval" in self.section_order:
            budget = max(0, min(
                self.section_budgets.get("retrieval", 2400),
                self.total_budget - len(section_texts[CURRENT_REQUEST_SECTION]) - 20,
            ))
            if budget and self._context_route["use_code_retrieval"]:
                try:
                    self._retrieval_result = self.agent.get_retrieval_engine().search(
                        user_message, budget_chars=budget, budget_tokens=budget
                    )
                except Exception:
                    self._retrieval_result = {"fallback_reason": "retrieval_unavailable", "hits": []}
            self.agent.last_retrieval = self._retrieval_result
            section_texts["retrieval"] = self._retrieval_result.get("text", "")
            evidence_cache = getattr(self.agent, "evidence_cache", None)
            if evidence_cache is not None:
                for path in evidence_cache.invalidate_stale():
                    if path not in self._stale_evidence_invalidations:
                        self._stale_evidence_invalidations.append(path)
                sources = [
                    source
                    for hit in self._retrieval_result.get("hits", [])
                    for source in hit.get("sources", [])
                ]
                # Direct mode already carries current source text.  Adding a cached
                # copy would spend context twice on the same range.
                if self._retrieval_result.get("strategy") != "direct":
                    self._source_evidence = evidence_cache.match_sources(sources, limit=3)
                if hasattr(self.agent, "session"):
                    self.agent.session["evidence_cache"] = evidence_cache.to_dict()

        if not context_reduction_enabled:
            rendered = self._render_sections_without_reduction(section_texts, selected_notes=selected_notes)
            prompt = self._assemble_prompt(rendered)
            metadata = self._metadata(
                prompt=prompt,
                rendered=rendered,
                budgets={section: render.budget for section, render in rendered.items() if section != CURRENT_REQUEST_SECTION},
                reduction_log=[],
                selected_notes=selected_notes,
                user_message=user_message,
                section_texts=section_texts,
            )
            return prompt, metadata

        budgets = dict(self.section_budgets)
        rendered = self._render_sections(section_texts, budgets, selected_notes=selected_notes)
        prompt = self._assemble_prompt(rendered)
        reduction_log = []

        # 如果 prompt 超预算，就按固定顺序不断压缩。
        # 这里的顺序体现了证据优先级：先压缩软记忆和候选，再压缩历史；
        # 新鲜源码证据晚于这些区域收缩，最后才动 working state 和 prefix。
        # 最新用户请求永远不裁剪，因为那是本轮最重要的输入。
        while len(prompt) > self.total_budget:
            overflow = len(prompt) - self.total_budget
            reduced = False
            for section in self.reduction_order:
                if rendered.get(section) is None or rendered[section].rendered_chars == 0:
                    continue
                floor = int(self.section_floors.get(section, 0))
                current_budget = int(budgets.get(section, 0))
                if current_budget <= floor:
                    continue
                new_budget = max(floor, current_budget - overflow)
                if new_budget >= current_budget:
                    continue
                reduction_log.append(
                    {
                        "section": section,
                        "before_chars": current_budget,
                        "after_chars": new_budget,
                        "overflow_chars": overflow,
                    }
                )
                budgets[section] = new_budget
                rendered = self._render_sections(section_texts, budgets, selected_notes=selected_notes)
                prompt = self._assemble_prompt(rendered)
                reduced = True
                break
            if not reduced:
                break

        metadata = self._metadata(
            prompt=prompt,
            rendered=rendered,
            budgets=budgets,
            reduction_log=reduction_log,
            selected_notes=selected_notes,
            user_message=user_message,
            section_texts=section_texts,
        )
        return prompt, metadata

    def _render_sections_without_reduction(self, section_texts, selected_notes=None):
        selected_notes = selected_notes or []
        relevant_lines = ["Recalled memory:"]
        if selected_notes:
            relevant_lines.extend(f"- {note['text']}" for note in selected_notes)
        else:
            relevant_lines.append("- none")
        relevant_raw = "\n".join(relevant_lines)
        history = list(getattr(self.agent, "session", {}).get("history", []))
        history_raw = self._raw_history_text(history)
        result = {
            "prefix": SectionRender(raw=section_texts["prefix"], budget=len(section_texts["prefix"]), rendered=section_texts["prefix"], details={}),
            "skill_catalog": SectionRender(raw=section_texts["skill_catalog"], budget=len(section_texts["skill_catalog"]), rendered=section_texts["skill_catalog"], details={}),
            "skills": SectionRender(raw=section_texts["skills"], budget=len(section_texts["skills"]), rendered=section_texts["skills"], details={}),
            "memory": SectionRender(raw=section_texts["memory"], budget=len(section_texts["memory"]), rendered=section_texts["memory"], details={}),
            "relevant_memory": SectionRender(
                raw=relevant_raw,
                budget=len(relevant_raw),
                rendered=relevant_raw if selected_notes else "",
                details={
                    "selected_notes": [note["text"] for note in selected_notes],
                    "rendered_notes": [note["text"] for note in selected_notes],
                    "selected_count": len(selected_notes),
                    "rendered_count": len(selected_notes),
                    "note_budget": 0,
                },
            ),
            "history": SectionRender(raw=history_raw, budget=len(history_raw), rendered=history_raw, details={"rendered_entries": []}),
            "run_control": SectionRender(
                raw=section_texts["run_control"],
                budget=len(section_texts["run_control"]),
                rendered=section_texts["run_control"],
                details={},
            ),
            CURRENT_REQUEST_SECTION: SectionRender(
                raw=section_texts[CURRENT_REQUEST_SECTION],
                budget=0,
                rendered=section_texts[CURRENT_REQUEST_SECTION],
                details={},
            ),
        }

        if "retrieval" in self.section_order:
            raw = section_texts.get("retrieval", "")
            result["retrieval"] = SectionRender(raw=raw, budget=len(raw), rendered=raw,
                details={"sources": [s for h in self._retrieval_result.get("hits", []) for s in h["sources"]]})
            result["source_evidence"] = self._render_source_evidence(
                self._source_evidence, sum(len(item["content"]) for item in self._source_evidence) + 512
            )
        return result

    def _compute_section_floors(self):
        floors = {
            section: max(20, int(budget) // 4)
            for section, budget in self.section_budgets.items()
        }
        floors.update(self._section_floor_overrides)
        return floors

    def _render_sections(self, section_texts, budgets, selected_notes=None):
        rendered = {}
        for section in self.section_order:
            budget = budgets.get(section)
            if section == CURRENT_REQUEST_SECTION:
                raw = section_texts[section]
                rendered[section] = SectionRender(raw=raw, budget=0, rendered=raw, details={})
            elif section == "relevant_memory":
                rendered[section] = self._render_relevant_memory(selected_notes or [], int(budget or 0))
            elif section == "retrieval":
                from .retrieval import pack_hits
                raw = section_texts.get("retrieval", "")
                text, hits = pack_hits(self._retrieval_result.get("hits", []), int(budget or 0), int(budget or 0))
                rendered[section] = SectionRender(raw=raw, budget=int(budget or 0), rendered=text, details={"sources": [s for h in hits for s in h["sources"]]})
            elif section == "source_evidence":
                rendered[section] = self._render_source_evidence(self._source_evidence, int(budget or 0))
            elif section == "history":
                rendered[section] = self._render_history_section(int(budget or 0))
            elif section == "skills":
                raw = section_texts.get(section, "")
                # Skill instructions are an atomic workflow contract.  Omit the
                # whole block if it cannot fit instead of clipping rules midway.
                rendered_text = raw if len(raw) <= int(budget or 0) else ""
                rendered[section] = SectionRender(
                    raw=raw,
                    budget=int(budget or 0),
                    rendered=rendered_text,
                    details={"whole_block": True, "omitted_for_budget": bool(raw and not rendered_text)},
                )
            elif section == "skill_catalog":
                raw = section_texts.get(section, "")
                rendered[section] = SectionRender(
                    raw=raw,
                    budget=int(budget or 0),
                    rendered=self._render_skill_catalog(raw, int(budget or 0)),
                    details={},
                )
            else:
                raw = section_texts[section]
                rendered_text = _tail_clip(raw, int(budget)) if budget is not None else raw
                rendered[section] = SectionRender(raw=raw, budget=int(budget) if budget is not None else 0, rendered=rendered_text, details={})
        return rendered

    def _render_relevant_memory(self, selected_notes, budget):
        header = "Recalled memory:"
        note_texts = [str(note.get("text", "")) for note in selected_notes if str(note.get("text", "")).strip()]
        raw_lines = [header] + [f"- {text}" for text in note_texts]
        raw = "\n".join(raw_lines) if note_texts else "\n".join([header, "- none"])
        if not note_texts:
            return SectionRender(
                raw=raw,
                budget=budget,
                rendered="",
                details={
                    "selected_notes": [],
                    "rendered_notes": [],
                    "selected_count": 0,
                    "rendered_count": 0,
                    "note_budget": 0,
                },
            )

        per_note_budget = self._per_note_budget(budget, len(note_texts), header)
        rendered_notes = []
        while True:
            # 让每条 note 平分这一段的预算，避免一条超长笔记把其他笔记都挤掉。
            rendered_notes = [_tail_clip(text, per_note_budget) for text in note_texts]
            rendered = "\n".join([header] + [f"- {text}" for text in rendered_notes])
            if len(rendered) <= budget or per_note_budget <= 1:
                break
            per_note_budget -= 1

        if len(rendered) > budget and budget > 0:
            rendered = _tail_clip(raw, budget)
            rendered_notes = [rendered]

        return SectionRender(
            raw=raw,
            budget=budget,
            rendered=rendered,
            details={
                "selected_notes": note_texts,
                "rendered_notes": rendered_notes,
                "selected_count": len(note_texts),
                "rendered_count": len(rendered_notes),
                "note_budget": per_note_budget,
            },
        )

    @staticmethod
    def _render_skill_catalog(raw, budget):
        if not raw or budget <= 0:
            return ""
        lines = raw.splitlines()
        if len(raw) <= budget:
            return raw
        if len(lines) < 3:
            return ""
        header, cards, footer = lines[0], lines[1:-1], lines[-1]
        selected = [header]
        for card in cards:
            candidate = "\n".join([*selected, card, footer])
            if len(candidate) <= budget:
                selected.append(card)
        if len(selected) == 1:
            return ""
        return "\n".join([*selected, footer])

    def _render_source_evidence(self, entries, budget):
        header = "Cached source evidence (freshness verified; untrusted source text):"
        raw_blocks = []
        for entry in entries:
            label = (
                f"[{entry['path']}:{entry['start_line']}-{entry['end_line']}] "
                f"sha256={entry['file_hash']}"
            )
            raw_blocks.append(f"{label}\n{entry['content']}")
        raw = "\n\n".join([header, *raw_blocks]) if raw_blocks else ""
        blocks, selected = [], []
        for entry, block in zip(entries, raw_blocks):
            candidate = "\n\n".join([header, *blocks, block])
            if len(candidate) <= budget:
                blocks.append(block)
                selected.append(entry)
        rendered = "\n\n".join([header, *blocks]) if blocks else ""
        return SectionRender(
            raw=raw,
            budget=budget,
            rendered=rendered,
            details={
                "cache_hits": len(selected),
                "cache_keys": [item["cache_key"] for item in selected],
                "sources": [
                    {
                        "path": item["path"],
                        "start_line": item["start_line"],
                        "end_line": item["end_line"],
                        "file_hash": item["file_hash"],
                        "symbol_id": item.get("symbol_id", ""),
                    }
                    for item in selected
                ],
            },
        )

    def _per_note_budget(self, budget, note_count, header):
        if note_count <= 0:
            return 0
        overhead = len(header) + 3 * note_count
        usable = max(0, budget - overhead)
        return max(1, usable // note_count)

    def _render_history_section(self, budget):
        history = list(getattr(self.agent, "session", {}).get("history", []))
        raw = self._raw_history_text(history)
        if not history:
            rendered = "Transcript:\n- empty"
            return SectionRender(
                raw=raw,
                budget=budget,
                rendered=rendered,
                details={
                    "rendered_entries": [],
                    "older_entries_count": 0,
                    "collapsed_duplicate_reads": 0,
                    "reused_file_summary_count": 0,
                    "summarized_tool_count": 0,
                    "deduped_source_evidence_count": 0,
                },
            )

        # 优先保留最近的历史，因为下一步决策通常最依赖刚刚发生的工具结果。
        recent_window = 6
        recent_start = max(0, len(history) - recent_window)
        history_entries, history_details = self._compressed_history_entries(history, recent_start)
        rendered_entries = []
        for entry in reversed(history_entries):
            recent = bool(entry.get("recent", False))
            candidate_lines = list(entry.get("lines", []))
            candidate_entries = candidate_lines + rendered_entries
            candidate_rendered = "\n".join(["Transcript:", *candidate_entries])
            if len(candidate_rendered) <= budget:
                rendered_entries = candidate_entries
                continue
            if recent:
                available = budget - len("Transcript:")
                if rendered_entries:
                    available -= sum(len(line) + 1 for line in rendered_entries)
                available = max(20, available - 1)
                if entry.get("retrieval_hits") is not None:
                    from .retrieval import pack_hits
                    prefix = "[tool:retrieve_code]"
                    text, _ = pack_hits(entry["retrieval_hits"], max(0, available-len(prefix)-1), max(0, available-len(prefix)-1))
                    candidate_lines = [prefix, text] if text else []
                    candidate_entries = candidate_lines + rendered_entries
                    if len("\n".join(["Transcript:", *candidate_entries])) <= budget:
                        rendered_entries = candidate_entries
                    continue
                candidate_lines = [_tail_clip(line, available) for line in candidate_lines]
                candidate_entries = candidate_lines + rendered_entries
                candidate_rendered = "\n".join(["Transcript:", *candidate_entries])
                if len(candidate_rendered) <= budget:
                    rendered_entries = candidate_entries
            else:
                smaller_lines = [_tail_clip(line, 20) for line in candidate_lines]
                smaller_entries = smaller_lines + rendered_entries
                smaller_rendered = "\n".join(["Transcript:", *smaller_entries])
                if len(smaller_rendered) <= budget:
                    rendered_entries = smaller_entries
        rendered = "\n".join(["Transcript:", *rendered_entries])

        if len(rendered) > budget and budget > 0:
            rendered = _tail_clip(raw, budget)

        return SectionRender(
            raw=raw,
            budget=budget,
            rendered=rendered,
            details={
                "recent_window": recent_window,
                "recent_start": recent_start,
                "rendered_entries": rendered_entries,
                **history_details,
            },
        )

    def _compressed_history_entries(self, history, recent_start):
        entries = []
        seen_older_reads = set()
        details = {
            "older_entries_count": 0,
            "collapsed_duplicate_reads": 0,
            "reused_file_summary_count": 0,
            "summarized_tool_count": 0,
            "deduped_source_evidence_count": 0,
        }

        for index, item in enumerate(history):
            if self._history_item_covered_by_source_evidence(item):
                details["deduped_source_evidence_count"] += 1
                continue
            recent = index >= recent_start
            if recent:
                line_limit = 900
                entries.append(
                    {
                        "recent": True,
                        "lines": self._render_history_item(item, line_limit),
                        **({"retrieval_hits": item["retrieval_hits"]} if "retrieval_hits" in item else {}),
                    }
                )
                continue

            if item["role"] == "tool" and item["name"] == "read_file":
                path = str(item["args"].get("path", "")).strip()
                if path in seen_older_reads:
                    details["collapsed_duplicate_reads"] += 1
                    continue
                seen_older_reads.add(path)
                summary = self._reusable_file_summary(path)
                if summary:
                    entries.append({"recent": False, "lines": [f"{path} -> {summary}"]})
                    details["older_entries_count"] += 1
                    details["reused_file_summary_count"] += 1
                    continue

            if item["role"] == "tool":
                summary_line = self._summarize_old_tool_item(item)
                entries.append({"recent": False, "lines": [summary_line]})
                details["older_entries_count"] += 1
                details["summarized_tool_count"] += 1
                continue

            entries.append({"recent": False, "lines": self._render_history_item(item, 60)})

        return entries, details

    def _history_item_covered_by_source_evidence(self, item):
        if item.get("role") != "tool" or item.get("name") not in {"read_file", "read_symbol"}:
            return False
        args = item.get("args", {})
        if item.get("name") == "read_symbol":
            symbol_id = str(args.get("symbol_id", "")).strip()
            return bool(symbol_id and any(entry.get("symbol_id") == symbol_id for entry in self._source_evidence))
        evidence_cache = getattr(self.agent, "evidence_cache", None)
        if evidence_cache is None:
            return False
        path = evidence_cache.canonical_path(args.get("path", ""))
        start_line = int(args.get("start", 1))
        end_line = int(args.get("end", 200))
        return any(
            entry.get("path") == path
            and start_line <= int(entry.get("start_line", 0))
            and end_line >= int(entry.get("end_line", 0))
            for entry in self._source_evidence
        )

    def _reusable_file_summary(self, path):
        evidence_cache = getattr(self.agent, "evidence_cache", None)
        if evidence_cache is not None:
            evidence_cache.invalidate_stale()
            canonical_path = evidence_cache.canonical_path(path)
            entries = [
                item for item in evidence_cache.to_dict().get("entries", [])
                if item.get("path") == canonical_path
            ]
            if entries:
                latest = max(entries, key=lambda item: item.get("access_index", 0))
                if latest.get("summary"):
                    return str(latest["summary"]).strip()
        memory = getattr(self.agent, "memory", None)
        if memory is None or not hasattr(memory, "to_dict"):
            return ""
        if hasattr(memory, "invalidate_stale_file_summaries"):
            memory.invalidate_stale_file_summaries()
        snapshot = memory.to_dict()
        summary = snapshot.get("file_summaries", {}).get(str(path), {})
        if not summary:
            return ""
        return str(summary.get("summary", "")).strip()

    def _summarize_old_tool_item(self, item):
        if item["name"] == "run_shell":
            command = str(item["args"].get("command", "")).strip() or "shell"
            lines = [line.strip() for line in str(item.get("content", "")).splitlines() if line.strip()]
            summary = " | ".join(lines[:3]) if lines else "(empty)"
            return f"{command} -> {summary}"
        return self._render_history_item(item, 60)[0]

    def _raw_history_text(self, history):
        if not history:
            return "Transcript:\n- empty"
        lines = []
        for item in history:
            if item["role"] == "tool":
                lines.append(f"[tool:{item['name']}] {json.dumps(item['args'], sort_keys=True)}")
                lines.append(str(item["content"]))
            else:
                lines.append(f"[{item['role']}] {item['content']}")
        return "\n".join(["Transcript:", *lines])

    def _render_history_item(self, item, line_limit):
        if item["role"] == "tool":
            prefix = f"[tool:{item['name']}] {json.dumps(item['args'], sort_keys=True)}"
            if "retrieval_hits" in item:
                from .retrieval import pack_hits
                # Keep full evidence initially; the section packer will drop whole blocks if needed.
                content, _ = pack_hits(item["retrieval_hits"], 4000, 4000)
                return [prefix, content]
            content = _tail_clip(item["content"], max(20, line_limit))
            return [prefix, content]
        return [f"[{item['role']}] {_tail_clip(item['content'], line_limit)}"]

    def _assemble_prompt(self, rendered):
        # 顺序是刻意设计的：稳定规则放前面，最新请求放最后。
        return "\n\n".join(rendered[section].rendered for section in self.section_order if rendered[section].rendered).strip()

    def _metadata(self, prompt, rendered, budgets, reduction_log, selected_notes, user_message, section_texts):
        section_metadata = {}
        for section in self.section_order[:-1]:
            section_metadata[section] = {
                "raw_chars": rendered[section].raw_chars,
                "budget_chars": int(budgets.get(section, 0)),
                "rendered_chars": rendered[section].rendered_chars,
            }
        section_metadata[CURRENT_REQUEST_SECTION] = {
            "raw_chars": len(section_texts[CURRENT_REQUEST_SECTION]),
            "budget_chars": None,
            "rendered_chars": len(rendered[CURRENT_REQUEST_SECTION].rendered),
        }
        return {
            "context_route": dict(self._context_route),
            "skill_route": {
                "mode": getattr(getattr(self.agent, "last_skill_route", None), "mode", "fallback"),
                "selected": list(getattr(getattr(self.agent, "last_skill_route", None), "selected", ())),
                "missing": list(getattr(getattr(self.agent, "last_skill_route", None), "missing", ())),
                "scores": dict(getattr(getattr(self.agent, "last_skill_route", None), "scores", {})),
                "active": list(getattr(self.agent, "session", {}).get("skills", {}).get("active", [])),
                "catalog_rendered_chars": rendered["skill_catalog"].rendered_chars,
                "rendered_chars": rendered["skills"].rendered_chars,
            },
            **({"retrieval": {**self.agent.retrieval_metadata(), "rendered_sources": rendered["retrieval"].details.get("sources", []), "rendered_chars": rendered["retrieval"].rendered_chars}} if "retrieval" in rendered else {}),
            **({"retrieval_candidates": {
                "rendered_chars": rendered["retrieval"].rendered_chars,
                "rendered_sources": rendered["retrieval"].details.get("sources", []),
            }} if "retrieval" in rendered else {}),
            **({"source_evidence": {
                "raw_chars": rendered["source_evidence"].raw_chars,
                "budget_chars": rendered["source_evidence"].budget,
                "rendered_chars": rendered["source_evidence"].rendered_chars,
                "cache_hits": int(rendered["source_evidence"].details.get("cache_hits", 0)),
                "cache_keys": list(rendered["source_evidence"].details.get("cache_keys", [])),
                "sources": list(rendered["source_evidence"].details.get("sources", [])),
                "stale_invalidations": len(self._stale_evidence_invalidations),
            }} if "source_evidence" in rendered else {}),
            "prompt_chars": len(prompt),
            "prompt_budget_chars": self.total_budget,
            "prompt_over_budget": len(prompt) > self.total_budget,
            "section_order": list(self.section_order),
            "section_budgets": {
                section: (None if section == CURRENT_REQUEST_SECTION else int(budgets.get(section, 0)))
                for section in self.section_order
            },
            "sections": section_metadata,
            "budget_reductions": reduction_log,
            "reduction_order": list(self.reduction_order),
            "relevant_memory": {
                "limit": RELEVANT_MEMORY_LIMIT,
                "selected_count": len(selected_notes),
                "selected_notes": [note["text"] for note in selected_notes],
                "selected_sources": [str(note.get("source", "")).strip() for note in selected_notes],
                "selected_kinds": [str(note.get("kind", "episodic")).strip() or "episodic" for note in selected_notes],
                "selected_durable_count": sum(
                    1 for note in selected_notes if (str(note.get("kind", "episodic")).strip() or "episodic") == "durable"
                ),
                "raw_chars": rendered["relevant_memory"].raw_chars,
                "rendered_chars": rendered["relevant_memory"].rendered_chars,
                "rendered_notes": list(rendered["relevant_memory"].details.get("rendered_notes", [])),
                "rendered_count": int(rendered["relevant_memory"].details.get("rendered_count", 0)),
            },
            "history": {
                "raw_chars": rendered["history"].raw_chars,
                "rendered_chars": rendered["history"].rendered_chars,
                "older_entries_count": int(rendered["history"].details.get("older_entries_count", 0)),
                "collapsed_duplicate_reads": int(rendered["history"].details.get("collapsed_duplicate_reads", 0)),
                "reused_file_summary_count": int(rendered["history"].details.get("reused_file_summary_count", 0)),
                "summarized_tool_count": int(rendered["history"].details.get("summarized_tool_count", 0)),
                "deduped_source_evidence_count": int(
                    rendered["history"].details.get("deduped_source_evidence_count", 0)
                ),
            },
            "current_request": {
                "text": user_message,
                "raw_chars": len(user_message),
                "rendered_chars": len(user_message),
                "section_chars": len(rendered[CURRENT_REQUEST_SECTION].rendered),
            },
        }
