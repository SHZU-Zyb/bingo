# Bingo 自适应多 Agent Workflow

## 1. 目标

Bingo 的多 Agent 设计解决两个具体问题：独立模块调查可以并行缩短等待时间；复杂诊断可以放进隔离上下文，避免大量推理过程和日志挤占父 Agent 的 Prompt。它不把每个步骤都包装成子 Agent。

运行时按以下顺序选择执行者：

```text
父 Agent 能直接完成？ ──是──> 父 Agent 执行
        │否
        v
本地算法能确定完成？ ──是──> 本地搜索 / 编辑 / 执行 / 解析
        │否
        v
存在 2~4 个独立跨模块调查？ ──是──> 并行只读子 Agent
        │否
        v
本地验证结果需要复杂归因？ ──是──> 单个诊断子 Agent
        │否
        v
父 Agent 继续通用流程
```

这套能力是原有 Runtime 的增量层。原来的单 Agent 控制循环、Memory、Evidence Cache、自适应代码检索、Skill、Checkpoint、审批、Trace 和 Report 都继续工作；`workflows` feature flag 可以整体关闭新增的三个工具。

## 2. 三种节点

| 节点 | 适合的工作 | 是否调用模型 | 是否可写源码 |
|---|---|---:|---:|
| `parent` | 任务规划、最终判断、统一编辑、整合答案 | 是 | 是，仍受审批策略控制 |
| `local_tool` | 搜索、读取、编辑、命令执行、计数、正则提取、工件落盘 | 否 | 只有父 Agent 发起的写工具可写 |
| `subagent` | 独立模块调查或复杂失败归因 | 是 | 否 |

父 Agent 是唯一源码写入者。子 Agent 的工具集合是显式交集，只包含文件列表、文件读取、搜索、Skill 按需加载和工作流工件的有界读取。子 Agent 看不到 `write_file`、`patch_file`、`run_shell`、`delegate`、`parallel_workflow` 或 `verification_workflow`。

## 3. 两种有界工作流

### 3.1 `parallel_explore`

调用入口是 `parallel_workflow`。它要求 2~4 个分支，每个分支包含稳定 ID、调查任务和作用域：

```json
{
  "objective": "分析认证改动的跨模块影响",
  "branches": [
    {"id": "api", "task": "检查 API 鉴权入口", "scope": ["bingo/api"]},
    {"id": "storage", "task": "检查令牌持久化", "scope": ["bingo/storage"]},
    {"id": "tests", "task": "检查已有回归覆盖", "scope": ["tests"]}
  ],
  "max_parallel": 3,
  "max_steps": 3
}
```

只有满足下列条件才应该调用：

- 至少两个模块；
- 分支之间不存在“必须等上一步结果”的依赖；
- 每个分支都有不重复的调查范围；
- 并行带来的时间收益或上下文隔离收益明显高于额外模型调用成本。

不适合并行的例子是“检索 → 编辑 → 测试 → 根据结果修复”。这些步骤共享状态并逐步依赖，应该由父 Agent 串行控制。本地搜索、编辑和测试执行也不需要子 Agent。

实现上，`WorkflowEngine` 用最多四个 worker 并行运行只读分支。每个分支获得独立的模型客户端实例、SessionStore、RunStore 和 ContextManager。真实模型客户端通过 `clone()` 复制配置但隔离每次调用元数据；测试客户端共享一个带锁的脚本队列，以便测试并发行为，同时仍隔离 `last_completion_metadata`。

每个子 Agent 返回结构化结果：

```json
{
  "summary": "API 层在路由装饰器中统一校验令牌",
  "findings": [
    {"severity": "high", "message": "刷新路径未复用校验器"}
  ],
  "evidence_refs": ["bingo/api/auth.py:42-71"]
}
```

完整返回写入 `result.json`。父 Agent 只看到经过字段白名单、条数限制和字符限制处理后的报告。

### 3.2 `verify_and_diagnose`

调用入口是 `verification_workflow`。命令始终由本地进程执行，不由子 Agent 执行：

```text
父 Agent 请求验证
    ↓
本地执行命令
    ↓
stdout/stderr 完整落盘
    ↓
正则解析状态、计数、失败卡片、异常名、源码位置
    ↓
门控判断
    ├─ 通过或单个清晰失败：直接返回父 Agent
    └─ 多失败 / 跨模块 / 缺少细节 / 非结构化失败：诊断子 Agent
```

本地解析结果示例：

```json
{
  "status": "failed",
  "exit_code": 1,
  "counts": {"passed": 18, "failed": 2, "skipped": 1, "errors": 0},
  "failures": [
    {
      "test_id": "tests/test_router.py::test_semantic_route",
      "message": "AssertionError: expected hybrid",
      "exception": "AssertionError",
      "source_refs": []
    }
  ],
  "source_refs": ["tests/test_router.py:41"]
}
```

门控规则由 `needs_diagnostic_agent()` 负责，当前触发原因包括：

| 原因 | 含义 |
|---|---|
| `multiple_failures` | 多个失败可能需要共同根因归并 |
| `cross_module_failure` | 失败跨越不同顶层模块 |
| `missing_failure_detail` | 找到了失败项但没有可用错误信息 |
| `unstructured_failure` | 命令失败，但本地规则无法提取失败卡片 |

一个清晰的失败不会调用模型诊断。例如 `FAILED tests/test_router.py::test_route - AssertionError: expected hybrid, got keyword` 已经给出测试位置、异常类型和差异，父 Agent 可以直接读取对应源码并修复。

诊断子 Agent 只收到解析后的失败卡片、源码位置和工件引用。只有这些信息不够时，它才调用 `read_workflow_artifact` 读取某一段原始日志。这比先把几万字符日志塞进模型，再要求模型摘要更节省上下文，也不会因为先截断日志而丢失可恢复信息。

## 4. 上下文压缩与完整信息

系统同时维护两个层次：

1. **无损工件层**：完整 stdout、stderr、子 Agent 返回和 workflow 状态写入磁盘。
2. **有损上下文层**：父 Agent 只接收固定字段、有限条目和最长 4,000 字符的合法 JSON。

压缩不是额外调用一个大模型。它由本地算法完成：

- pytest 计数和失败行由正则提取；
- 子 Agent 返回只保留 `summary`、有限的 `findings` 和 `evidence_refs`；
- 单项摘要、错误信息和引用分别设字符上限；
- 超过总预算时先移除低优先级 findings，再缩短摘要；
- 最后仍超限则只返回 workflow ID、状态和按需读取提示。

这样不会把“截断后的内容”误当成完整事实。每个压缩结果保留 `workflow_id` 和 `artifact_ref`，父 Agent 可以调用：

```json
{
  "workflow_id": "wf_123",
  "node_id": "runner",
  "artifact": "stderr.log",
  "start_line": 120,
  "end_line": 180
}
```

单次最多读取 500 行、4,000 字符。WorkflowStore 对 workflow ID、节点名和文件名做白名单检查，并对解析后的路径做根目录包含校验，阻止 `../` 和符号链接逃逸。

## 5. 工件与 Session

在一次正常 `ask()` 运行中，工作流工件位于：

```text
.bingo/runs/<run_id>/workflows/<workflow_id>/
├── workflow.json
├── runner/
│   ├── stdout.log
│   └── stderr.log
├── api/
│   ├── result.json
│   ├── sessions/
│   └── runs/
└── diagnostician/
    ├── result.json
    ├── sessions/
    └── runs/
```

直接调用 Runtime 工具且当前没有 run 时，工件放在 `.bingo/workflows/`。Session 的 `workflows.items` 只保存状态、计数、失败卡片、诊断摘要、证据位置和工件根目录，不保存 stdout/stderr 正文。父 Agent 的普通 History 只收到压缩后的工具结果。

## 6. 失败与降级边界

| 情况 | 行为 |
|---|---|
| 只有一个并行分支 | 拒绝，交给父 Agent |
| 超过四个分支 | 拒绝，要求合并范围或分批 |
| 分支 ID 或范围重复 | 拒绝，避免伪并行和重复上下文 |
| 分支路径逃逸仓库 | 工具参数校验阶段拒绝 |
| 某个子 Agent 失败 | 其他分支继续，workflow 标为 `partial_failed` |
| 测试通过 | 本地返回，诊断子 Agent 不启动 |
| 单个清晰失败 | 本地返回失败卡片 |
| 诊断门控触发但模型不可用 | 保留本地结果与完整日志；工具报告诊断执行错误 |
| 命令超时 | 退出码记为 124，超时信息进入 stderr 工件 |
| 压缩信息不足 | 通过工件引用按行读取，不重新执行命令 |

并发子 Agent 之间不共享 Session、History、ContextManager 或模型调用元数据。它们共享同一个只读工作区，因此不能承担编辑职责。父 Agent 等待所有独立调查结束后统一判断和写入，避免并发修改冲突。

## 7. 与既有模块的关系

| 模块 | 职责 | 在 Workflow 中的位置 |
|---|---|---|
| Memory | 保存跨轮次的决策、偏好和任务事实 | 不存放原始测试日志 |
| Adaptive Retrieval | 定位与查询相关的代码 Symbol | 父 Agent 按需检索；子 Agent可用基础搜索和读取 |
| Evidence Cache | 复用已读且哈希仍新鲜的源码证据 | 父 Agent整合调查后继续使用 |
| Skill | 告诉 Agent 某类任务应该怎样做 | `multi-agent-workflow` 提供路由规则 |
| Workflow Engine | 管理并发、验证、诊断门控和压缩报告 | 新增控制层 |
| Workflow Store | 保存无损输出并提供有界读取 | 上下文之外的证据层 |

工作流不会替代自适应检索。检索回答“应该读取哪些代码”，Workflow 回答“这项工作由父 Agent、本地算法还是子 Agent 执行”。Memory 也不会被拿来缓存日志；它继续保存值得跨轮次复用的事实。

## 8. 关键源码

| 文件 | 作用 |
|---|---|
| `bingo/workflow_types.py` | 可序列化 Workflow、节点和失败记录 |
| `bingo/verification_parser.py` | 本地验证结果解析与诊断门控 |
| `bingo/workflow_store.py` | 完整工件写入和安全有界读取 |
| `bingo/workflow_engine.py` | 并行调查与验证诊断模板 |
| `bingo/models.py` | 子 Agent 模型客户端隔离复制 |
| `bingo/tools.py` | 工具 schema、参数校验和执行入口 |
| `bingo/runtime.py` | 子 Agent 构造、能力限制、Session 压缩记录和 Prompt 规则 |
| `.bingo/skills/multi-agent-workflow/` | 模型可按需加载的路由指南 |

## 9. 测试策略

`tests/test_workflows.py` 覆盖：

- 模型客户端 clone 的状态隔离；
- pytest 计数、失败卡片、异常名和源码位置提取；
- 通过、清晰单失败、多失败和非结构化失败的门控；
- 两个 worker 确实并发执行；
- 并行分支数量边界；
- 完整日志落盘和按行有界读取；
- 路径穿越拒绝；
- 清晰失败不调用诊断子 Agent；
- 复杂失败只把结构化事实交给诊断子 Agent；
- Runtime 工具注册、Session 不保存 stdout；
- 子 Agent 看不到写入、命令、委派和工作流工具；
- 父 Agent 能根据引用读取完整子 Agent 工件。

最终验证命令和最新结果记录在本文末尾，提交或演示前应重新运行，避免把历史数字当成当前结论。

## 10. 面试讲法

可以用下面这段话介绍：

> 我没有把多 Agent 做成固定流水线，而是在 Runtime 增加了执行者路由。单 Agent 能完成的留在父 Agent，确定性的搜索、编辑、执行和测试解析交给本地算法；只有 2 到 4 个真正独立的跨模块调查才并发创建只读子 Agent。测试也不交给子 Agent跑，本地进程先执行并用正则提取计数和失败卡片，只有多失败、跨模块或非结构化错误才创建诊断 Agent。完整日志和子 Agent 输出无损落盘，父 Agent只拿 4,000 字符以内的结构化摘要和位置引用，不够时再按行读取工件。这样并发用于缩短关键路径，子 Agent用于隔离复杂推理，上下文控制由可解释的本地门控完成。

如果面试官追问“为什么不直接让一个模型总结日志”，可以回答：本地解析对计数和标准失败格式更快、更稳定且零模型成本；无损日志保留在工件层，摘要不完整时仍可精确回读；模型只处理规则难以归因的部分。

## 11. 最新验证结果

验证日期：2026-09-10。

| 检查 | 结果 |
|---|---|
| Workflow 专项测试 | `14 passed in 0.46s` |
| 全量 pytest | `218 passed, 2 skipped in 93.20s` |
| Python 编译检查 | `python -m compileall -q bingo tests/test_workflows.py` 通过 |
| Ruff（新增模块、模型、工具、公开导出和测试） | `All checks passed!` |
| Runtime Ruff 基础错误与导入检查 | `ruff check bingo/runtime.py --select F,I` 通过 |
| CLI 启动 | `python -m bingo --help` 正常显示参数 |

四个 skip 是项目已有的可选场景，不是 Workflow 失败。完整回归覆盖原有检索、Memory、Evidence Cache、Skill、Checkpoint、审批、安全边界、CLI 与旧 delegate 行为。
