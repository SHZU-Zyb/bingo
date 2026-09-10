# 🐱 bingo 项目完全学习指南

> 适合零基础新人，从项目结构到核心架构，循序渐进理解整个项目。

---

## 一、一句话概括

**bingo 是一个轻量级的本地 Coding Agent**：你在终端里给它一句话（比如 "帮我修复这个测试"），它会自动读取你的代码仓库、调用工具（读文件/改文件/跑命令），多轮迭代直到完成任务，然后把结果返回给你。

---

## 二、项目全景图

```
bingo-main/
├── bingo/                    ← 核心源码（13个模块）
│   ├── __init__.py          ← 包的公开接口
│   ├── __main__.py          ← python -m bingo 入口
│   ├── cli.py               ← 命令行参数解析 & 启动装配
│   ├── config.py            ← .env 配置加载
│   ├── models.py            ← 模型后端适配（Ollama/OpenAI/Anthropic/DeepSeek）
│   ├── runtime.py           ← 🏠 Agent 核心控制循环（最重要！）
│   ├── workspace.py         ← 工作区快照（Git 状态、项目文档）
│   ├── tools.py             ← 工具定义（读文件、写文件、搜索、shell...）
│   ├── context_manager.py   ← Prompt 组装 & 上下文预算管理
│   ├── memory.py            ← 工作记忆层（文件摘要、笔记、长期记忆）
│   ├── task_state.py        ← 任务状态机
│   ├── run_store.py         ← 运行工件落盘（trace/report）
│   ├── metrics.py           ← 实验与指标采集
│   └── evaluator.py         ← Benchmark 评估框架
├── tests/                   ← 测试（~2700行）
│   ├── test_bingo.py         ← 核心运行时测试（1630行，最重要）
│   ├── test_context_manager.py
│   ├── test_evaluator.py
│   ├── test_memory.py
│   ├── test_metrics.py
│   ├── test_run_store.py
│   ├── test_safety_invariants.py
│   └── test_task_state.py
├── benchmarks/              ← Benchmark 任务定义
│   └── coding_tasks.json   ← 12个固定回归任务
├── scripts/                 ← 实验脚本
├── assets/                  ← 截图
├── pyproject.toml           ← Python 项目配置（零第三方依赖！）
└── .env.example             ← 环境变量模板
```

---

## 三、核心架构：一次请求的完整链路

当你输入 `bingo "帮我修复 test_user.py 的报错"` 时，数据在以下模块间流动：

```
用户输入
  │
  ▼
cli.py                    ← 解析命令行参数，选择模型后端
  │
  ▼
runtime.py :: Bingo.__init__()   ← 装配 Agent：workspace + model + session + tools + memory
  │
  ▼
runtime.py :: Bingo.ask()        ← 🏠 核心控制循环
  │
  ├── 1. context_manager.py     ← 组装 prompt（prefix + memory + history + 用户请求）
  │
  ├── 2. models.py              ← 把 prompt 发给 LLM，拿回原始文本
  │
  ├── 3. runtime.py :: parse()  ← 解析模型输出（<tool> 还是 <final>？）
  │
  ├── 4. tools.py               ← 如果是工具调用：校验参数 → 审批 → 执行 → 记录结果
  │
  ├── 5. memory.py              ← 从工具结果中提取关键信息，写进工作记忆
  │
  └── 6. 回到步骤 1，循环直到模型返回 <final> 或达到步数上限
  │
  ▼
最终答案返回给用户
```

---

## 四、模块详解（按学习优先级排序）

### 🔴 第一梯队（必须首先理解）

#### 1. `runtime.py` — Agent 的"大脑"

这是整个项目的**核心文件**（~1350行），包含两个关键类：

| 类 | 作用 |
|---|---|
| `Bingo` | Agent 主体。包含控制循环 `ask()`、工具执行 `run_tool()`、模型输出解析 `parse()`、记忆更新等所有核心逻辑 |
| `SessionStore` | 会话的读写。会话保存在 `.bingo/sessions/{session_id}.json` |

**关键属性（`__init__` 方法）**：

```python
class Bingo:
    def __init__(self, ...):
        self.model_client        # 模型后端（Ollama/OpenAI/Anthropic/DeepSeek）
        self.workspace           # 工作区快照（Git 信息 + 项目文档）
        self.session_store       # 会话持久化
        self.approval_policy     # 审批策略：ask / auto / never
        self.max_steps           # 最大工具调用步数（默认6）
        self.max_new_tokens      # 每次模型调用最大输出 token（默认512）
        self.depth / max_depth   # 子 Agent 递归深度控制
        self.read_only           # 只读模式
        self.tools               # 工具注册表（由 tools.py 构建）
        self.prefix              # prompt 的稳定前缀
        self.memory              # 分层记忆（LayeredMemory 实例）
        self.context_manager     # prompt 组装与预算控制
        self.run_store           # 运行工件落盘
```

**`ask()` 方法——最核心的控制循环**（`runtime.py:756-998`）：

```python
def ask(self, user_message):
    # 记录用户消息到 history
    self.record({"role": "user", "content": user_message, ...})

    # 创建任务状态
    task_state = TaskState.create(...)

    # ============ 主循环 ============
    while tool_steps < self.max_steps and attempts < max_attempts:

        # 1. 组装 prompt（prefix + memory + history + user_message）
        prompt, metadata = self._build_prompt_and_metadata(user_message)

        # 2. 调用模型
        raw = self.model_client.complete(prompt, self.max_new_tokens, ...)

        # 3. 解析模型输出
        kind, payload = self.parse(raw)

        # 4a. 如果是工具调用 → 执行 → 记录 → 继续循环
        if kind == "tool":
            result = self.run_tool(payload["name"], payload["args"])
            self.record({"role": "tool", ...})
            continue

        # 4b. 如果解析失败 → 让模型重试
        if kind == "retry":
            continue

        # 4c. 如果是最终答案 → 返回
        if kind == "final":
            return final

    # 达到停止条件（步数上限或重试上限）
    return stop_message
```

**`parse()` 方法——模型输出的结构化解析**（`runtime.py:1211-1261`）：

模型必须返回以下两种格式之一：

- **工具调用（JSON 风格）**：`<tool>{"name":"read_file","args":{"path":"main.py"}}</tool>`
- **工具调用（XML 风格）**：`<tool name="write_file" path="x.py"><content>...</content></tool>`
- **最终答案**：`<final>修复完成</final>`

解析返回 `(kind, payload)` 元组，其中 `kind` 取值：
- `"tool"` → payload 是 `{"name": "read_file", "args": {...}}`
- `"final"` → payload 是最终答案字符串
- `"retry"` → payload 是错误提示，让模型重试

**`run_tool()` 方法——工具执行防线**（`runtime.py:1000-1127`）：

每个工具调用必须经过以下防线：

```
1. 工具是否存在？        → 不在注册表 → 返回 "unknown tool"
2. 参数是否合法？        → 校验失败   → 返回 "invalid arguments"
3. 是否连续两次相同调用？ → 是         → 返回 "repeated call"（防死循环）
4. 是否需要审批？        → 用户拒绝   → 返回 "approval denied"
5. 执行前拍快照          → 用于检测文件变更
6. 真正执行工具          → 返回结果
7. 执行后拍快照          → 对比前后差异
8. 更新工作记忆          → 提取关键信息
```

**其他重要方法**：

| 方法 | 作用 |
|---|---|
| `build_prefix()` | 生成 prompt 的稳定前缀（系统指令 + 工具列表 + 工作区信息） |
| `refresh_prefix()` | 当工作区变化时刷新前缀 |
| `build_tools()` | 通过 `toolkit.build_tool_registry()` 构建工具注册表 |
| `create_checkpoint()` | 在关键节点创建任务检查点，支持恢复 |
| `evaluate_resume_state()` | 评估恢复状态（文件是否过期、工作区是否漂移） |
| `promote_durable_memory()` | 将工作记忆提升为长期记忆 |
| `redact_text()` / `redact_artifact()` | 脱敏敏感信息 |
| `capture_workspace_snapshot()` | 拍摄工作区文件快照（用于 diff） |
| `reset()` | 清空会话历史和记忆 |

#### 2. `tools.py` — Agent 的"双手"

定义了 Agent 可以执行的 **7种工具**（`tools.py:14-51`）：

| 工具 | 风险等级 | 说明 |
|---|---|---|
| `list_files` | 安全 | 列出目录内容，过滤掉 `.git`、`__pycache__` 等 |
| `read_file` | 安全 | 按行号范围读取 UTF-8 文件，返回带行号的内容 |
| `search` | 安全 | 优先使用 `ripgrep`，回退到 Python 实现 |
| `run_shell` | ⚠️ 危险 | 在仓库根目录执行 shell 命令（timeout 1-120秒） |
| `write_file` | ⚠️ 危险 | 写入文本文件（自动创建父目录） |
| `patch_file` | ⚠️ 危险 | 精确替换一段文本（`old_text` 必须恰好出现1次） |
| `delegate` | 安全 | 创建只读子 Agent 去调查问题（受 `max_depth` 限制） |

**`build_tool_registry()` 函数**（`tools.py:64-75`）：

```python
def build_tool_registry(agent):
    tools = {}
    for name, spec in BASE_TOOL_SPECS.items():
        tools[name] = {
            **spec,
            "run": partial(_TOOL_RUNNERS[name], agent)  # 绑定 agent 实例
        }
    # 子 Agent 只在 depth < max_depth 时可用
    if agent.depth < agent.max_depth:
        tools["delegate"] = {**DELEGATE_TOOL_SPEC, "run": partial(tool_delegate, agent)}
    return tools
```

**`patch_file` 的严格性设计**（`tools.py:125-139`）：

```python
# old_text 必须在文件中恰好出现一次
# 如果出现 0 次 → 错误："已经改过了？"
# 如果出现 2+ 次 → 错误："不够精确，需要更多上下文"
text = path.read_text(encoding="utf-8")
count = text.count(old_text)
if count != 1:
    raise ValueError(f"old_text must occur exactly once, found {count}")
```

**`delegate`（子 Agent 委派）**（`tools.py:261-288`）：

```python
def tool_delegate(agent, args):
    child = Bingo(
        model_client=agent.model_client,
        workspace=agent.workspace,
        approval_policy="never",      # 子 agent 不请求审批
        max_steps=3,                   # 步数较少
        depth=agent.depth + 1,         # 深度+1
        read_only=True,                # 只读！
    )
    return "delegate_result:\n" + child.ask(task)
```

#### 3. `workspace.py` — Agent 的"眼睛"

在 Agent 读取任何文件之前，先通过 `git` 收集一份"仓库第一印象"：

```python
class WorkspaceContext:
    cwd              # 当前工作目录
    repo_root        # Git 仓库根目录
    branch           # 当前分支
    default_branch   # 默认分支（如 origin/main）
    status           # git status --short
    recent_commits   # 最近5条 git log --oneline
    project_docs     # 预加载的文档（AGENTS.md, README.md, pyproject.toml, package.json）
```

这些信息被拼成一段固定文本（`prefix`），作为每轮 prompt 的**稳定前缀**，支持 prompt cache 复用。

**`fingerprint()` 方法**：对工作区状态做 SHA256 哈希，用于判断是否需要重建 prefix。

---

### 🟡 第二梯队（理解后能懂得 prompt 是怎么拼出来的）

#### 4. `context_manager.py` — Prompt 的"拼图师"

决定每轮到底把多少内容塞进 prompt，以及超出预算时怎么裁剪。

**Prompt 结构**：

```
最终 Prompt = prefix + memory + relevant_memory + history + current_request
```

**预算分配（默认值）**：

| Section | 预算（字符） | 最低保障 |
|---|---|---|
| prefix | 3600 | 1200 |
| memory | 1600 | 400 |
| relevant_memory | 1200 | 300 |
| history | 5200 | 1500 |
| current_request | 无限 | 永不裁剪 |
| **总计** | **12000** | - |

**预算收缩优先级**（`reduction_order`）：

```
relevant_memory → history → memory → prefix  （永远不裁 current_request）
```

**关键方法**：

| 方法 | 作用 |
|---|---|
| `build(user_message)` | 主入口：组装完整 prompt 并返回 metadata |
| `_render_sections()` | 按预算渲染各 section |
| `_render_history_section()` | 历史压缩：优先保留最近6条，旧条目做总结 |
| `_render_relevant_memory()` | 按预算均分显示相关笔记 |
| `_compressed_history_entries()` | 旧历史压缩：去重 read_file、用文件摘要替代原文 |

#### 5. `memory.py` — Agent 的"短期+长期记忆"

分层设计：

| 层级 | 存储结构 | 容量限制 | 持久化 |
|---|---|---|---|
| **Working Memory** | `task_summary` + `recent_files` | 8个文件 | session.json |
| **File Summaries** | `{path: {summary, freshness}}` | 6条显示 | session.json |
| **Episodic Notes** | `[{text, tags, source, kind}]` | 12条 | session.json |
| **Durable Memory** | `.bingo/memory/topics/*.md` | 无限制 | 磁盘文件 |

**`LayeredMemory` 类——统一入口**：

```python
class LayeredMemory:
    # 任务相关
    set_task_summary(summary)
    remember_file(path)

    # 笔记
    append_note(text, tags, source, kind)
    retrieval_candidates(query, limit)   # 关键词召回相关笔记
    retrieval_view(query, limit)         # 格式化召回结果

    # 文件摘要
    set_file_summary(path, summary)
    invalidate_file_summary(path)
    invalidate_stale_file_summaries()    # 检测文件是否变化

    # 长期记忆
    promote_durable(promotions)          # 提升到持久存储

    # 渲染
    render_memory_text()                 # 给模型看的紧凑仪表盘
```

**长期记忆的四种主题**（`DURABLE_TOPIC_DEFAULTS`）：

| 主题 | 说明 |
|---|---|
| `project-conventions` | 项目约定 |
| `key-decisions` | 关键决策与理由 |
| `dependency-facts` | 依赖与环境事实 |
| `user-preferences` | 用户偏好 |

**长期记忆提升的过滤规则**（`reject_durable_reason()`）：
- 包含 `<redacted>` 或疑似密钥 → 拒绝（`secret_shaped`）
- 看起来像 checkpoint 状态 → 拒绝（`transient_task_state`）
- 太长或包含 stdout/stderr → 拒绝（`noisy_output`）

#### 6. `models.py` — 模型后端的"翻译官"

把不同 LLM 提供商的 API 差异抹平，对外暴露统一的 `complete(prompt, max_new_tokens) -> text` 接口：

| Client 类 | 后端 | API 端点 | 特殊处理 |
|---|---|---|---|
| `OllamaModelClient` | 本地 Ollama | `/api/generate` | 无 prompt cache |
| `OpenAICompatibleModelClient` | OpenAI / right.codes | `/v1/responses` | 支持 SSE、prompt cache |
| `AnthropicCompatibleModelClient` | Anthropic / DeepSeek | `/v1/messages` | `x-api-key` 认证头 |
| `FakeModelClient` | 测试用 | 无 | 预定义输出列表 |

**关键细节**：

- `OpenAICompatibleModelClient` 支持自动重试（3次），遇到 5xx 错误会退避重试
- SSE（Server-Sent Events）响应的解析逻辑较复杂，需要处理 `text/event-stream` 格式
- `AnthropicCompatibleModelClient` 目前**不启用 prompt cache**（`supports_prompt_cache = False`）
- URL 自动补全 `/v1` 后缀（`_normalize_versioned_base_url()`）

---

### 🟢 第三梯队（支撑性模块）

#### 7. `cli.py` — 命令行入口

负责将"用户怎么启动 bingo"翻译成 runtime 能理解的对象：

```
命令行参数 → argparse 解析 → 选择模型后端 → 构建 WorkspaceContext
→ 加载 .env → 整理 secret 名单 → 创建/恢复 SessionStore
→ 创建 ModelClient → 装配 Bingo 实例 → 进入 one-shot 或 REPL 循环
```

**关键函数**：

| 函数 | 作用 |
|---|---|
| `build_arg_parser()` | 定义所有 CLI 参数 |
| `_build_model_client(args)` | 根据 `--provider` 创建对应的 ModelClient |
| `_effective_model(args, provider)` | 模型选择优先级：CLI参数 > 环境变量 > 默认值 |
| `build_agent(args)` | 装配出完整的 Bingo 实例 |
| `build_welcome(agent, model, host)` | 生成启动欢迎界面 |
| `main(argv)` | 程序入口：one-shot 或 REPL 循环 |

**REPL 内置命令**：

| 命令 | 作用 |
|---|---|
| `/help` | 查看帮助 |
| `/memory` | 查看工作记忆 |
| `/session` | 查看会话文件路径 |
| `/reset` | 清空会话历史与记忆 |
| `/exit` 或 `/quit` | 退出 |

#### 8. `task_state.py` — 任务状态跟踪

一次性 `ask()` 调用中的状态快照：

```python
@dataclass
class TaskState:
    run_id: str          # 运行 ID
    task_id: str         # 任务 ID
    user_request: str    # 用户请求原文
    status: str          # running / completed / stopped / failed
    tool_steps: int      # 真正执行了多少次工具
    attempts: int        # 模型被调用了多少轮（包含解析失败的）
    last_tool: str       # 最后一次调用的工具名
    stop_reason: str     # 停止原因
    final_answer: str    # 最终答案
    checkpoint_id: str   # 关联的检查点 ID
    resume_status: str   # 恢复状态
```

**停止原因枚举**：

| 值 | 含义 |
|---|---|
| `final_answer_returned` | 正常完成 |
| `step_limit_reached` | 达到步数上限 |
| `retry_limit_reached` | 达到重试上限 |
| `model_error` | 模型返回错误 |
| `tool_timeout` | 工具执行超时 |
| `approval_denied` | 审批被拒绝 |

#### 9. `run_store.py` — 运行工件落盘

每次 `ask()` 在 `.bingo/runs/{run_id}/` 下生成3个文件：

| 文件 | 格式 | 内容 |
|---|---|---|
| `task_state.json` | JSON | 任务状态快照（原子写入） |
| `trace.jsonl` | JSONL | 事件时间线（逐条追加写入） |
| `report.json` | JSON | 最终摘要（原子写入） |

**原子写入**：先写临时文件，再 `replace`，避免中途崩溃留下半截 JSON。

#### 10. `config.py` — 配置加载

从项目根目录的 `.env` 文件加载环境变量：

- **向上查找**：从当前目录向上遍历，找到第一个 `.env`
- **格式支持**：`KEY=VALUE` 和 `export KEY=VALUE`
- **引号去除**：自动去除单引号和双引号
- **override 控制**：可控制是否覆盖已有环境变量

---

### 🔵 高级模块

#### 11. `evaluator.py` — Benchmark 框架

用**脚本化的假模型**（`FakeModelClient`）跑固定的 benchmark 任务。

**核心类 `BenchmarkEvaluator`**：

- 加载 `benchmarks/coding_tasks.json` 中的任务定义
- 为每个任务创建独立的 fixture 副本（`shutil.copytree`）
- 用 `FakeModelClient`（预定义输出序列）替代真实模型
- 执行任务后通过 verifier 脚本验证结果
- 输出 benchmark artifact（JSON）

**判断通过的四个条件**：
```
passed = within_budget AND verifier_passed AND expected_artifact_exists AND non_failure_stop_reason
```

**Benchmark 任务的 setup 类型**：
- `context_reduction`：模拟长上下文触发压缩
- `freshness_mismatch`：模拟文件过期触发 re-anchor
- `workspace_mismatch`：模拟工作区漂移触发恢复

#### 12. `metrics.py` — 实验与指标

包含多个实验套件，支持 **synthetic（合成）** 和 **real（真实模型）** 两种模式：

| 实验 | 目的 |
|---|---|
| Context Ablation | 测试上下文压缩前后的 prompt 大小和正确率 |
| Memory Experiment | 测试记忆层是否减少重复读文件 |
| Large-scale Memory Experiment | 12个任务 × 3种变体的规模化记忆测试 |
| Security Experiment | 10个安全场景的防护有效性测试 |
| Recovery Ablation | 测试 checkpoint/resume 恢复机制 |
| Provider Experiments | 跨模型提供商的 benchmark 对比 |

**实验变体**（经典的三组对照）：
- `memory_on`：正常启用记忆
- `memory_off`：关闭记忆（对照）
- `memory_irrelevant`：注入无关记忆（干扰）

---

## 五、关键设计理念

### 1. 零第三方依赖

```toml
# pyproject.toml
[project]
dependencies = []  # 只依赖 Python 标准库！
```

只用 `urllib`、`subprocess`、`json`、`pathlib`、`hashlib` 等标准库。这意味着：
- 不需要 `pip install` 任何东西就能跑核心逻辑
- 没有依赖冲突
- 代码即文档，每个 HTTP 请求都可见

### 2. 显式工具注册

工具不是动态发现的，而是在 `tools.py` 的 `BASE_TOOL_SPECS` 字典中显式注册：

```python
BASE_TOOL_SPECS = {
    "list_files": {"schema": {...}, "risky": False, "description": "..."},
    "read_file":  {"schema": {...}, "risky": False, "description": "..."},
    # ... 每个工具都有明确的 schema、风险等级、描述
}
```

这让 Agent 看到的动作集合是**可审计、有边界**的。

### 3. 路径沙箱

所有文件操作通过 `agent.path(raw_path)` 解析（`runtime.py:1338-1346`）：

```python
def path(self, raw_path):
    path = Path(raw_path)
    path = path if path.is_absolute() else self.root / path
    resolved = path.resolve()  # 解析符号链接
    # 强制锚定在 workspace root 下
    if os.path.commonpath([str(self.root), str(resolved)]) != str(self.root):
        raise ValueError(f"path escapes workspace: {raw_path}")
    return resolved
```

防止 `../` 逃逸和符号链接绕过。

### 4. 审批模式

危险工具（`run_shell`、`write_file`、`patch_file`）有三种审批策略：

| 策略 | CLI 参数 | 行为 |
|---|---|---|
| `ask` | `--approval ask` | 每次危险操作前询问用户 |
| `auto` | `--approval auto` | 自动放行 |
| `never` | `--approval never` | 拒绝所有危险操作（等同于 read_only） |

### 5. 稳定前缀缓存

`WorkspaceContext` 生成的 prefix 是 prompt 的稳定部分：

```
prefix (稳定, 可缓存)     → "你是 bingo，一个编码 agent。\n工具：...\n工作区：..."
memory (半稳定)           → "Memory:\n- task: 修复测试\n- recent_files: test.py"
relevant_memory (动态)    → "Relevant memory:\n- test.py 第42行有断言错误"
history (动态)            → "Transcript:\n[tool:read_file] ..."
current_request (动态)    → "Current user request:\n帮我修复 test_user.py 的报错"
```

配合 `prompt_cache_key`（prefix 的 SHA256），支持 OpenAI 兼容后端的 prompt cache 跨轮复用。

### 6. 分层记忆

```
Durable Memory (.bingo/memory/topics/*.md)
    ↑ promote_durable()
Episodic Notes (12条上限)
    ↑ append_note()
File Summaries (6条显示)
    ↑ set_file_summary()
Working Memory (task_summary + recent_files)
```

### 7. 敏感信息保护

- **环境变量白名单**：shell 执行时只传递安全的环境变量
- **Secret 检测**：基于变量名模式（`API_KEY`、`TOKEN`、`SECRET`）自动识别
- **脱敏**：trace 和 report 中自动替换敏感值为 `<redacted>`
- **长期记忆过滤**：拒绝存储包含疑似密钥的内容

---

## 六、数据流详解

### 6.1 Prompt 的组装过程

```
context_manager.py :: build(user_message)
    │
    ├── 从 agent.prefix          → prefix section ("你是 bingo...")
    ├── 从 agent.memory_text()   → memory section ("Memory: ...")
    ├── 从 agent.checkpoint      → 追加到 prefix（如果有检查点）
    ├── 从 memory.retrieval()    → relevant_memory section ("Relevant memory: ...")
    ├── 从 agent.history_text()  → history section ("Transcript: ...")
    └── 用户输入                 → current_request section
    │
    └── _assemble_prompt() → 拼成最终 prompt
    └── 如果超预算 → 按 reduction_order 裁剪
```

### 6.2 模型调用的完整路径

```
Bingo.ask()
  → _build_prompt_and_metadata(user_message)
    → refresh_prefix()
    → evaluate_resume_state()
    → context_manager.build(user_message)
  → model_client.complete(prompt, max_new_tokens, prompt_cache_key, prompt_cache_retention)
    → HTTP 请求发送到 provider API
    → 解析响应（JSON 或 SSE）
    → 提取文本和 usage/cache 元数据
  → parse(raw)
    → 正则匹配 <tool> 或 <final> 标签
    → 返回 (kind, payload)
```

### 6.3 会话持久化

```
session.json 结构:
{
  "id": "20260526-210738-2dc473",
  "created_at": "2026-05-26T21:07:38+00:00",
  "workspace_root": "/path/to/repo",
  "history": [
    {"role": "user", "content": "...", "created_at": "..."},
    {"role": "tool", "name": "read_file", "args": {...}, "content": "...", "created_at": "..."},
    {"role": "assistant", "content": "...", "created_at": "..."}
  ],
  "memory": {
    "working": {"task_summary": "...", "recent_files": ["..."]},
    "episodic_notes": [{"text": "...", "tags": [...], ...}],
    "file_summaries": {"path": {"summary": "...", "freshness": "sha256:..."}}
  },
  "checkpoints": {
    "current_id": "ckpt_abc123",
    "items": {"ckpt_abc123": {...}}
  },
  "runtime_identity": {...},
  "resume_state": {...}
}
```

---

## 七、学习路线建议

### 第一周：跑起来

```bash
# 1. 配置环境
cp .env.example .env
# 编辑 .env，填入你的 API key

# 2. 安装（零依赖，直接装）
pip install -e .

# 3. 启动交互模式（推荐 DeepSeek）
uv run bingo --provider deepseek

# 4. 在 REPL 里尝试简单任务
bingo> 这个项目是做什么的？
bingo> 帮我看看 tests/ 目录下有哪些测试文件
bingo> /memory    ← 查看 Agent 的学习笔记
bingo> /help      ← 查看内置命令
bingo> /exit
```

### 第二周：理解核心链路

**阅读顺序**：

1. **`runtime.py` 的 `ask()` 方法**（第756行）← 最重要的入口
   - 理解 while 循环的四个步骤：组 prompt → 调模型 → 解析 → 执行工具
   - 理解停止条件：步数上限 vs 重试上限 vs 正常完成

2. **`tools.py` 全文**（~300行）
   - 看每种工具的参数 schema 和校验逻辑
   - 看 `patch_file` 为什么要求 `old_text` 恰好出现一次
   - 看 `delegate` 如何创建受限子 Agent

3. **`workspace.py` 全文**（~135行）
   - 看 `WorkspaceContext.build()` 如何通过 git 命令采集信息
   - 理解 `fingerprint()` 如何用于判断 prefix 是否需要重建

4. **`context_manager.py` 的 `build()` 方法**（第78行）
   - 看 5 个 section 如何被组装
   - 看预算超出时如何按优先级裁剪

### 第三周：理解周边机制

5. **`memory.py`**
   - 先看 `LayeredMemory` 类的公开方法
   - 再看 `DurableMemoryStore` 的磁盘读写
   - 理解 `retrieval_candidates()` 的关键词召回逻辑

6. **`models.py`**
   - 对比 `OpenAICompatibleModelClient` 和 `AnthropicCompatibleModelClient` 的差异
   - 看 SSE 解析的容错设计
   - 看 prompt cache 参数的传递

7. **`cli.py`**
   - 从 `main()` 函数跟踪完整启动流程
   - 理解 `build_agent()` 如何装配所有组件

### 第四周：深入测试与评估

8. **跑测试**
   ```bash
   uv run pytest tests/ -v
   ```

9. **读 `evaluator.py`**
   - 看 benchmark 任务的完整生命周期
   - 理解 `FakeModelClient` 如何模拟模型行为

10. **尝试扩展**
    - 给项目添加一个新工具（如在 `tools.py` 中加 `git_diff`）
    - 写对应的测试
    - 跑 benchmark 验证

---

## 八、最核心的一句话总结

> **Bingo = `cli.py` 装配参数 → `runtime.py` 的控制循环反复执行 "组 prompt→调模型→解析输出→执行工具→更新记忆" → 直到模型说 `<final>`**

如果你只读两个文件，优先读：
1. **`runtime.py`** — 控制循环 + 解析 + 工具执行防线
2. **`tools.py`** — 7 种工具的定义和执行

理解了这两个，bingo 的骨架就清楚了。

---

## 九、快速参考

### 项目元信息

| 项目 | 值 |
|---|---|
| 包名 | `bingo` |
| CLI 命令 | `bingo` |
| Python 入口 | `python -m bingo` |
| Python 版本要求 | 3.10+ |
| 第三方依赖 | 0 |
| 会话目录 | `.bingo/sessions/` |
| 运行工件目录 | `.bingo/runs/{run_id}/` |
| 长期记忆目录 | `.bingo/memory/topics/` |

### 模型后端速查

```bash
# Ollama（本地）
uv run bingo --provider ollama --model qwen3.5:4b

# OpenAI 兼容
uv run bingo --provider openai

# Anthropic 兼容
uv run bingo --provider anthropic

# DeepSeek（推荐）
uv run bingo --provider deepseek
```

### 环境变量

```bash
# OpenAI
BINGO_OPENAI_API_BASE=https://www.right.codes/codex/v1
BINGO_OPENAI_API_KEY=your-key
BINGO_OPENAI_MODEL=gpt-5.4

# Anthropic
BINGO_ANTHROPIC_API_BASE=https://www.right.codes/claude/v1
BINGO_ANTHROPIC_API_KEY=your-key
BINGO_ANTHROPIC_MODEL=claude-sonnet-4-6

# DeepSeek
BINGO_DEEPSEEK_API_BASE=https://api.deepseek.com/anthropic
BINGO_DEEPSEEK_API_KEY=your-key
BINGO_DEEPSEEK_MODEL=deepseek-v4-pro
```

### 工具速查

```bash
# 列出文件
<tool>{"name":"list_files","args":{"path":"."}}</tool>

# 读取文件
<tool>{"name":"read_file","args":{"path":"main.py","start":1,"end":100}}</tool>

# 搜索代码
<tool>{"name":"search","args":{"pattern":"def test_","path":"."}}</tool>

# 执行命令
<tool>{"name":"run_shell","args":{"command":"pytest -q","timeout":30}}</tool>

# 写入文件
<tool name="write_file" path="new.py"><content>print('hello')</content></tool>

# 修改文件
<tool name="patch_file" path="main.py"><old_text>old line</old_text><new_text>new line</new_text></tool>

# 最终答案
<final>修复完成！</final>
```
