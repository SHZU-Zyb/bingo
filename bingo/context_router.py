"""Explainable routing across task state, recalled memory and repository search."""

from __future__ import annotations

import re

CODE_PATTERN = re.compile(
    r"(?i)(code|function|method|class|implement|repository|file|bug|error|where|"
    r"\.py\b|\.ts\b|\.js\b|函数|方法|类|文件|仓库|实现|报错|检索|调用|定义|修改|哪里)"
)
MEMORY_PATTERN = re.compile(
    r"(?i)(previous|earlier|last time|remember|recall|agreed|decision|preference|convention|"
    r"why did we|之前|上次|记得|回忆|约定|决定|偏好|为什么决定|按照之前)"
)
RESUME_PATTERN = re.compile(r"(?i)(continue|resume|pick up|继续|恢复任务|接着做)")


def route_context(
    query,
    *,
    auto_retrieve=False,
    memory_enabled=True,
    relevant_memory_enabled=True,
    memory_candidate_count=0,
):
    text = str(query)
    code_intent = bool(CODE_PATTERN.search(text))
    explicit_memory_intent = bool(MEMORY_PATTERN.search(text))
    resume_intent = bool(RESUME_PATTERN.search(text))
    use_code = bool(auto_retrieve and code_intent)
    use_memory = bool(
        memory_enabled
        and relevant_memory_enabled
        and (explicit_memory_intent or int(memory_candidate_count) > 0)
    )
    reasons = []
    if code_intent:
        reasons.append("code_intent")
    if explicit_memory_intent:
        reasons.append("memory_intent")
    if resume_intent:
        reasons.append("resume_intent")
    if memory_candidate_count:
        reasons.append("memory_candidate_match")
    if code_intent and (explicit_memory_intent or use_memory):
        intent = "mixed"
    elif code_intent:
        intent = "code"
    elif explicit_memory_intent or use_memory:
        intent = "memory"
    elif resume_intent:
        intent = "resume"
    else:
        intent = "general"
    return {
        "intent": intent,
        "use_task_state": True,
        "use_memory_recall": use_memory,
        "use_code_retrieval": use_code,
        "use_evidence_cache": use_code,
        "reasons": reasons or ["general_request"],
    }
