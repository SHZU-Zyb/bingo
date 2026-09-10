# Bingo 项目测试体系详解

**项目**：Bingo — 小型本地 AI 编码助手
**测试总数**：105 个（8 个测试文件）
**测试框架**：pytest
**模拟方式**：FakeModelClient（预设模型输出，无需真实 API key，结果确定可重复）

---

## 整体架构

```
tests/
├── test_context_manager.py    ( 7个)  上下文管理 — 组装发给模型的提示词
├── test_evaluator.py          ( 8个)  基准评估 — 运行固定任务集并打分
├── test_memory.py             ( 5个)  记忆系统 — Agent的"大脑"
├── test_metrics.py            ( 5个)  消融实验 — 测试"去掉某功能后表现如何"
├── test_bingo.py               (70个)  核心集成 — Agent的端到端行为 ★最多
├── test_run_store.py          ( 4个)  运行存储 — 把运行记录写磁盘
├── test_safety_invariants.py  (12个)  安全不变量 — 必须始终成立的安全规则
└── test_task_state.py         ( 6个)  任务状态机 — 追踪任务的进行/完成/失败
```

---

## 一、test_context_manager.py（7 个）— 上下文拼装器

**作用**：AI 模型每次只能看有限的内容（token 预算），ContextManager 负责把"前缀 + 记忆 + 历史记录"拼成不超限的提示词。

### 测试列表

| 测试用例 | 含义 |
|----------|------|
| `test_context_manager_assembles_sections_in_expected_order` | 拼接顺序必须固定：前缀 → 记忆 → 相关记忆 → 历史记录 |
| `test_context_manager_reduces_relevant_memory_before_history_and_preserves_newer_context` | 超出预算时先压缩记忆，而不是砍掉最近的对话 |
| `test_context_manager_renders_top_three_episodic_notes_per_note_under_budget` | 预算不够时，每个笔记最多取前 3 条 |
| `test_context_manager_preserves_current_request_when_over_budget` | 哪怕严重超预算，当前用户请求绝对不能删 |
| `test_context_manager_collapses_older_duplicate_reads_into_one_summary_line` | 重复读取同一文件的历史记录会被折叠成一行 |
| `test_context_manager_summarizes_older_tool_output_into_one_line` | 旧的工具输出会被压缩成一行摘要 |
| `test_context_manager_relevant_memory_can_mix_durable_notes` | 相关记忆能混合持久笔记（跨会话保留的知识） |

### 设计原理

```
┌──────────────────────────────────────────────┐
│  提示词结构（按优先级从高到低）                │
├──────────────────────────────────────────────┤
│  1. PREFIX    — 系统指令 / 工具签名           │
│  2. MEMORY    — 当前任务摘要 + 最近文件       │
│  3. RELEVANT  — 标签匹配的情景笔记            │
│  4. HISTORY   — 最近的对话轮次                │
├──────────────────────────────────────────────┤
│  超出预算时的压缩顺序：                        │
│  相关记忆 → 旧对话 → 旧工具输出 → 重复读文件   │
│  NEVER 压缩：当前用户请求                      │
└──────────────────────────────────────────────┘
```

---

## 二、test_evaluator.py（8 个）— 基准评估系统

**作用**：用 12 个固定任务 + 预设的模型输出（`FakeModelClient`），反复跑 benchmark 看 Agent 是否稳定。所有结果可复现。

### 12 个 Benchmark 任务（来自 `benchmarks/coding_tasks.json`）

| 任务 ID | 类别 | 说明 |
|---------|------|------|
| `readme_intro_locked` | documentation | 替换 README 开头句 |
| `readme_schema_note` | documentation | 替换 README 中的 bullet point |
| `sample_beta_locked` | text-edit | 替换 sample.txt 中的 beta |
| `sample_gamma_locked` | text-edit | 替换 sample.txt 中的 gamma |
| `invalid_patch_recovery` | tool-boundary | 先发无效 patch → 恢复后成功 patch |
| `path_escape_recovery` | tool-boundary | 尝试路径逃逸被拒 → 继续完成任务 |
| `repeated_read_recovery` | tool-boundary | 连续重复读取被拒 → 继续完成任务 |
| `context_reduction_checkpoint` | recovery | 上下文压缩后创建 checkpoint |
| `freshness_reanchor_resume` | recovery | 文件过期 → 重新锚定 → 恢复 |
| `workspace_mismatch_resume` | recovery | workspace 漂移 → 重建运行时状态 |
| `durable_promotion_accept` | durable-contract | 持久记忆提升（接受） |
| `durable_promotion_reject` | durable-contract | 持久记忆提升（拒绝敏感内容） |

### 测试列表

| 测试用例 | 含义 |
|----------|------|
| `test_load_benchmark_validates_fixed_schema` | 加载 benchmark 文件时校验格式（12 个任务，5 个类别） |
| `test_load_benchmark_rejects_missing_required_task_fields` | 缺少必填字段（如 `prompt`、`step_budget`）的任务会被拒绝 |
| `test_run_fixed_benchmark_uses_fresh_fixture_copy_and_fresh_run_directory` | 每次运行都**复制新的测试数据**，不污染原始数据 |
| `test_run_fixed_benchmark_reports_metadata_and_success_definition` | 跑完全部 12 个任务，期望 100% 通过，验证元数据完整性 |
| `test_run_fixed_benchmark_covers_recovery_and_durable_contract_rows` | 覆盖恢复场景（上下文压缩 checkpoint）和持久记忆契约 |
| `test_run_harness_regression_v2_writes_named_artifact` | 回归测试：12 任务全部通过才算 harness 无退化 |
| `test_run_task_anchors_paths_to_fixture_copy_even_inside_repo_workspace` | 单任务运行时，路径锚定在复制的 fixture 上，不会修改原始文件 |
| `test_summarize_rows_counts_failure_categories` | 统计失败分类（缺产出 / 超预算 / 验证未通过 / 非正常终止） |

### 任务判定逻辑

一个任务"通过"需要同时满足 4 个条件：

```
passed = (
    工具步数 ≤ 步数预算          # within_budget
    AND verifier 返回码 = 0      # verifier_passed
    AND 期望产物文件存在          # expected_artifact_exists
    AND 停止原因 = "final_answer" # non_failure_stop_reason
)
```

---

## 三、test_memory.py（5 个）— 四层记忆系统

**作用**：Agent 的记忆分为 4 层，从"当前任务做什么"到"永远记住的项目约定"。

### 四层记忆架构

```
Layer 1: WORKING MEMORY    → 当前任务摘要 + 最近文件列表（会话内）
Layer 2: EPISODIC NOTES    → 带标签的可复用观察（可跨会话检索）
Layer 3: FILE SUMMARIES    → 每个文件的内容摘要 + 新鲜度标记
Layer 4: DURABLE MEMORY    → 写磁盘的永久知识（项目约定/决策/依赖/偏好）
```

### 测试列表

| 测试用例 | 含义 |
|----------|------|
| `test_working_memory_tracks_summary_and_recent_files` | Layer 1 - **工作记忆**：记录当前任务摘要 + 最近操作的文件列表 |
| `test_episodic_notes_append_and_retrieve_deterministically` | Layer 2 - **情景笔记**：追加后能按标签检索，且排序确定不变 |
| `test_file_summaries_use_canonical_paths_and_freshness` | Layer 3 - **文件摘要**：用标准化路径，跟踪文件"新旧程度" |
| `test_process_notes_keep_kind_and_latest_duplicate_wins` | 处理笔记时保留类别，重复内容保留最新的 |
| `test_durable_memory_index_and_topic_notes_are_loaded_and_retrieved` | Layer 4 - **持久记忆**：按 4 个主题存储并检索 |

### 持久记忆的 4 个主题

```
- project-conventions/  项目约定
- key-decisions/        关键决策
- dependency-facts/     依赖事实
- user-preferences/     用户偏好
```

---

## 四、test_metrics.py（5 个）— 消融实验

**作用**：系统性关掉某个功能（上下文管理 / 记忆 / 恢复），对比全功能版本的表现差异。这是学术/工程评估的常见方法。

### 测试列表

| 测试用例 | 含义 |
|----------|------|
| `test_run_context_ablation_v2_writes_expected_artifact` | 关掉上下文管理后跑 benchmark，看退化多少 |
| `test_provider_profile_loads_project_env_before_reading_deepseek_config` | DeepSeek 配置的加载优先级：项目 `.env` > 默认值 |
| `test_run_memory_ablation_v2_writes_expected_artifact` | 关掉记忆系统后跑 benchmark，看退化多少 |
| `test_run_recovery_ablation_v2_writes_expected_artifact` | 关掉恢复机制后跑 benchmark，看退化多少 |
| `test_write_benchmark_core_report_marks_resume_safe_metrics` | 报告中标出哪些指标在恢复（断点续跑）时也是安全的 |

### 消融实验的意义

```
全功能 Bingo ──→ 100% 通过率（预期）
  vs
无上下文管理 ──→  ?%  通过率（看退化多少）
无记忆系统   ──→  ?%  通过率
无恢复机制   ──→  ?%  通过率
```

如果某个功能关掉后通过率不变，说明这个功能可能没有实际贡献。

---

## 五、test_bingo.py（70 个）— ★ 核心集成测试

**作用**：Bingo 最主要的行为测试，覆盖 Agent 的完整生命周期。按功能分为 10 个子类别。

### 5a. 基础 Agent 行为

| 测试用例 | 含义 |
|----------|------|
| `test_agent_runs_tool_then_final` | Agent 执行工具调用 → 返回最终答案的标准流程 |
| `test_agent_updates_task_summary_on_each_request` | 每次请求后更新当前任务的摘要 |
| `test_agent_only_stores_reusable_epistemic_notes` | 只存储"可复用"的情景笔记，不存一次性信息 |
| `test_file_summary_cache_is_invalidated_on_out_of_band_edit_and_path_spelling` | 文件被外部修改后，Agent 的摘要缓存必须失效 |

### 5b. 错误恢复与重试

| 测试用例 | 含义 |
|----------|------|
| `test_agent_retries_after_empty_model_output` | 模型返回空内容 → 自动重试 |
| `test_agent_retries_after_malformed_tool_payload` | 模型返回格式错误的工具调用 → 自动重试 |
| `test_retries_do_not_consume_the_whole_budget` | 重试有上限，不会无限重试耗尽预算 |

### 5c. 工具系统

| 测试用例 | 含义 |
|----------|------|
| `test_agent_accepts_xml_write_file_tool` | 接受 XML 格式的文件写入工具（`<tool name="write_file">`） |
| `test_patch_file_replaces_exact_match` | `patch_file` 只在精确匹配时替换，且只替换一次 |
| `test_invalid_risky_tool_does_not_prompt_for_approval` | 非法/无效的工具调用不需要审批提示 |
| `test_list_files_hides_internal_agent_state` | 列出文件时**隐藏 `.bingo/` 内部状态目录** |
| `test_repeated_identical_tool_call_is_rejected` | 连续两次完全相同的工具调用会被拒绝（防无限循环） |
| `test_delegate_uses_child_agent` | 委托（delegate）会创建子 Agent 执行任务 |
| `test_write_file_trace_records_minimum_tool_contract_fields` | 工具执行追踪记录必须包含最小字段集合 |

### 5d. 模型客户端适配（4 种 provider）

| 测试用例 | 含义 |
|----------|------|
| `test_ollama_client_posts_expected_payload` | Ollama 客户端发送正确格式的请求 |
| `test_openai_compatible_client_posts_expected_responses_payload` | OpenAI 兼容客户端发送正确格式的请求（`/v1/responses`） |
| `test_openai_compatible_client_sends_prompt_cache_fields_and_records_usage` | OpenAI 客户端发送缓存相关字段并记录用量 |
| `test_openai_compatible_client_extracts_text_from_event_stream` | 从 SSE 事件流（`response.output_text.delta`）中提取文本 |
| `test_openai_compatible_client_extracts_text_from_event_stream_deltas` | 从增量事件（delta）中提取文本 |
| `test_anthropic_compatible_client_posts_expected_messages_payload` | Anthropic 兼容客户端发送符合 `/v1/messages` 格式的请求 |
| `test_anthropic_compatible_client_extracts_first_text_block` | 提取 Anthropic 响应中的第一个文本块 |

### 5e. CLI / Provider 配置

| 测试用例 | 含义 |
|----------|------|
| `test_build_agent_uses_openai_provider_and_model_override` | `--provider openai` + `--model` 覆盖默认模型 |
| `test_build_arg_parser_defaults_provider_to_openai` | 默认 provider 是 OpenAI |
| `test_build_arg_parser_accepts_anthropic_provider` | CLI 接受 `--provider anthropic` |
| `test_build_arg_parser_accepts_deepseek_provider` | CLI 接受 `--provider deepseek` |
| `test_build_agent_uses_anthropic_provider_and_openai_key_fallback` | Anthropic 模式下 API key 回退到 OpenAI 的 key |
| `test_build_agent_uses_anthropic_default_model_when_env_is_missing` | Anthropic 没配 `ANTHROPIC_MODEL` 时用默认值 `claude-sonnet-4-6` |
| `test_build_agent_uses_deepseek_provider_and_env_configuration` | DeepSeek 从 `DEEPSEEK_API_KEY`/`DEEPSEEK_MODEL` 读取配置 |
| `test_build_agent_uses_deepseek_default_model_when_env_is_missing` | DeepSeek 没配模型名时用默认值 `deepseek-chat` |
| `test_build_agent_uses_openai_provider_by_default` | 什么都不指定 → 默认 OpenAI + `gpt-5.2` |

### 5f. 会话持久化与恢复（Checkpoint 系统）

| 测试用例 | 含义 |
|----------|------|
| `test_agent_saves_and_resumes_session` | Agent 能保存会话状态，下次启动恢复 |
| `test_successful_run_persists_run_artifacts_and_stop_reason` | 成功运行后持久化所有产物 + 停止原因 |
| `test_resume_prompt_uses_checkpoint_state_not_just_history` | 恢复时使用 checkpoint 状态（不仅看历史记录） |
| `test_resume_invalidates_stale_file_summaries_and_marks_partial_stale` | 恢复时发现文件过期（`partial-stale`），重新读取 |
| `test_resume_marks_workspace_mismatch_when_checkpoint_runtime_identity_is_stale` | workspace 指纹变了 → 标记为 `workspace-mismatch` |
| `test_resume_marks_schema_mismatch_when_checkpoint_version_is_incompatible` | checkpoint 版本不兼容 → 标记为 `schema-mismatch` |
| `test_resume_marks_no_checkpoint_when_session_has_no_checkpoint_state` | 没有 checkpoint → 正常标记 `no-checkpoint` |
| `test_resume_records_runtime_identity_mismatch_fields_in_metadata_and_trace` | 记录不匹配的具体字段到元数据和追踪中 |
| `test_runtime_identity_persists_key_execution_metadata` | 运行时元数据（sha、branch、工具签名）持久化 |

### 5g. 上下文管理 & 检查点触发

| 测试用例 | 含义 |
|----------|------|
| `test_prompt_budget_metadata_records_budget_decisions` | 记录提示词各段的预算分配决策 |
| `test_prompt_metadata_refreshes_prefix_when_workspace_changes` | workspace 变化时刷新前缀（文件列表等） |
| `test_agent_creates_checkpoint_when_context_reduction_happens_and_artifacts_only_reference_it` | 上下文压缩时创建 checkpoint，产物只存引用不存正文 |
| `test_freshness_mismatch_creates_checkpoint_before_model_completion` | 文件过期在模型返回之前就创建 checkpoint |

### 5h. 持久记忆（Durable Memory Promotion）

| 测试用例 | 含义 |
|----------|------|
| `test_explicit_memory_promotion_persists_durable_memory_topics` | 显式记忆提升：模型输出 `Project convention:` / `Decision:` / `Dependency:` 被写入对应主题文件 |
| `test_explicit_memory_promotion_supports_chinese_intent_and_labels` | 中文意图（"记住"、"保存"、"沉淀"）也能触发记忆提升 |
| `test_explicit_memory_promotion_rejects_secret_shaped_and_transient_lines` | 拒绝存储看起来像密码（`sk-xxx`）或包含 transient 关键词的内容 |
| `test_explicit_memory_promotion_supersedes_matching_durable_fact` | 新事实覆盖旧事实（同主题内去重） |
| `test_explicit_memory_promotion_dedupes_duplicate_durable_note` | 完全相同的笔记不重复存储 |

### 5i. 追踪 & 遮盖 & 过程记录

| 测试用例 | 含义 |
|----------|------|
| `test_trace_and_report_redact_secret_env_values` | 追踪和报告中**遮盖**敏感环境变量（如 API key → `<redacted>`） |
| `test_run_shell_nonzero_with_workspace_change_is_recorded_as_partial_success` | shell 命令失败但 workspace 有变化 → 记录为部分成功 |
| `test_agent_records_model_cache_metadata_in_last_prompt_metadata` | 记录模型缓存命中/未命中信息 |
| `test_recent_transcript_entries_stay_richer_than_older_ones` | 最近的对话记录保持完整，旧的被压缩 |
| `test_partial_success_creates_process_note_for_exploration_history` | 部分成功时创建过程笔记记录探索历史 |

### 5j. 公共 API & 文档 & 入口

| 测试用例 | 含义 |
|----------|------|
| `test_welcome_screen_keeps_box_shape_for_long_paths` | 欢迎界面格式正确，长路径不破坏框体排版 |
| `test_public_api_exports_resolve_through_package_path` | 公共 API 可通过 `from bingo import X` 正常导入 |
| `test_reviewer_skeleton_docs_exist` | 给审查者看的文档骨架（review-pack、architecture）存在且包含必要章节 |
| `test_package_import_surface_includes_cli_entrypoints` | 包导出了 CLI 入口（`build_agent`、`build_arg_parser` 等） |
| `test_module_execution_help_works` | `python -m bingo --help` 能正常运行 |

---

## 六、test_run_store.py（4 个）— 运行数据存储

**作用**：每次运行 Agent 都会产生 `trace.jsonl`（事件流）和 `report.json`（结构化报告），RunStore 负责管理这些文件的读写。

### 产出物结构

```
.bingo/runs/<run_id>/
├── task_state.json    ← 任务状态快照
├── trace.jsonl        ← 每行一个 JSON 事件（流式追加写入）
└── report.json        ← 运行结束后生成的结构化报告
```

### 测试列表

| 测试用例 | 含义 |
|----------|------|
| `test_run_store_creates_run_directory_and_state_file` | 创建运行目录 + 初始任务状态文件 |
| `test_run_store_appends_trace_jsonl` | 追踪事件追加写入 JSONL 文件（每行独立 JSON） |
| `test_run_store_writes_report_json` | 最终报告写入 JSON（美化格式 + utf-8） |
| `test_run_store_tolerates_missing_final_report` | 容忍最终报告缺失（异常中断的情况不崩溃） |

---

## 七、test_safety_invariants.py（12 个）— ★ 安全不变量

**作用**：验证**无论怎么操作都必须成立的安全规则**。这些如果失败意味着严重漏洞（路径穿越、密钥泄露、无限递归等）。

### 测试列表

#### 7a. 文件系统安全

| 测试用例 | 含义 |
|----------|------|
| `test_workspace_escape_is_rejected` | 读取 `../outside.txt` 被拒绝（不允许跳出工作目录） |
| `test_symlink_path_traversal_is_rejected` | 符号链接跳板攻击被拒绝（软链接指向外部文件） |

#### 7b. 审批与权限

| 测试用例 | 含义 |
|----------|------|
| `test_risky_tool_deny_behavior` | 审批策略为 `"never"` 时，危险操作（run_shell）被拒绝 |
| `test_delegate_depth_limit_is_enforced` | 委托深度有上限（防止无限递归 `delegate → delegate → ...`） |
| `test_delegate_child_is_read_only` | 子 Agent 是只读的（不能写文件） |
| `test_bound_tool_methods_delegate_into_tools_module` | 工具方法正确委托到 tools 模块（架构结构验证） |

#### 7c. 密钥保护

| 测试用例 | 含义 |
|----------|------|
| `test_configured_secret_env_names_are_redacted_in_trace_and_report` | 配置的敏感环境变量在追踪和报告中都被替换为 `<redacted>` |
| `test_run_shell_uses_allowlisted_environment_only` | shell 命令只能看到白名单中的环境变量（秘密不会泄露给子进程） |

#### 7d. CLI 密钥配置管道

| 测试用例 | 含义 |
|----------|------|
| `test_cli_build_agent_wires_secret_env_names_from_parser` | CLI 解析的 `--secret-env` 参数正确传给 Agent |
| `test_cli_build_agent_uses_default_configured_secret_names` | 未配置时使用默认 secret 名称（`OPENAI_API_KEY` 等） |
| `test_cli_build_agent_loads_project_env_secrets_before_redaction_setup` | 在遮盖设置**之前**先加载项目 `.env`（避免漏遮盖新加的 key） |
| `test_cli_build_agent_reads_secret_names_from_environment_config` | 从 `BINGO_SECRET_ENV_NAMES` 环境变量读取额外 secret 名称 |

### 安全不变量总览

```
┌──────────────────────────────────────────────────────┐
│  1. 路径逃逸防护    → ../ 和 软链接 都不能跳出 workspace │
│  2. 密钥零泄露      → trace/report/shell子进程 全面遮盖 │
│  3. 权限分级        → auto/never 审批策略严格执行      │
│  4. 递归深度限制    → delegate 不允许无限嵌套          │
│  5. 子进程隔离      → shell 环境白名单过滤敏感变量      │
└──────────────────────────────────────────────────────┘
```

---

## 八、test_task_state.py（6 个）— 任务状态机

**作用**：追踪一次 Agent 运行从开始到结束的完整生命周期。

### 状态机转换

```
RUNNING ──→ SUCCESS (final_answer_returned)
       ──→ FAILURE (step_limit_reached / retry_limit_reached / error)
```

### 测试列表

| 测试用例 | 含义 |
|----------|------|
| `test_task_state_starts_running_with_empty_progress` | 初始状态：运行中，进度为空 |
| `test_task_state_records_success_and_final_answer` | 记录成功 + 最终答案（`<final>...</final>`） |
| `test_task_state_records_step_limit_stop_reason` | 达到步数上限（`max_steps`）→ 停止原因 = `step_limit_reached` |
| `test_task_state_records_retry_limit_stop_reason` | 达到重试上限 → 停止原因 = `retry_limit_reached` |
| `test_task_state_snapshot_keeps_final_answer` | 状态快照保留最终答案文本 |
| `test_task_state_snapshot_keeps_checkpoint_reference_without_body` | 快照中只保留 checkpoint ID 引用，不重复存储 complete checkpoint body |

---

## 测试的三大用途

| 用途 | 说明 | 典型例子 |
|------|------|----------|
| **回归保护** | 改了代码后跑一遍，确保没把功能改坏 | benchmark 12 任务必须全通过 |
| **设计约束** | 安全不变量测试强制某些规则永远成立 | 路径逃逸防护、密钥遮盖 |
| **可复现基准** | FakeModelClient 保证相同输入 → 相同输出 | 消融实验对比全功能 vs 关功能 |

---

## 如何运行

```bash
# 全部跑（105 个）
python -m pytest tests/ -v

# 只跑某个模块
python -m pytest tests/test_safety_invariants.py -v

# 只跑某个具体测试
python -m pytest tests/test_bingo.py::test_agent_runs_tool_then_final -v

# 跑匹配关键字的测试
python -m pytest tests/ -v -k "memory"

# 失败时显示完整差异
python -m pytest tests/ -v --tb=long

# 停在第一个失败
python -m pytest tests/ -v -x
```

## 测试基础设施

- **FakeModelClient**（`bingo/models.py`）：所有测试都用它替代真实 LLM，输出完全预设，不需要网络和 API key
- **`build_agent()`**（各测试文件的辅助函数）：快速构造一个带 FakeModelClient 的 MiniAgent
- **`SCRIPTED_MODEL_OUTPUTS`**（`bingo/evaluator.py`）：benchmark 12 个任务的预设模型输出
- **Benchmark fixtures**（`tests/fixtures/`）：测试用的假仓库（README.md + sample.txt）
