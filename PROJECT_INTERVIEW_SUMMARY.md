# Bingo 项目面试讲解总结

这份文档用于日常复习和面试前梳理。目标不是死背文件名，而是能把这个项目讲成一个完整、内行、流畅的工程故事。

## 1. 项目一句话定位

`bingo` 是一个运行在本地代码仓库里的轻量级 Coding Agent。它不是普通聊天机器人，而是在大模型外面实现了一层可控 runtime：每一轮先采集 workspace 状态和历史上下文，组装 prompt，然后让模型在受限工具集中选择动作；工具执行结果会写回 session history、working memory、checkpoint 和 trace，下一轮模型再基于最新上下文继续决策。

面试开场可以这样说：

> 我这个项目实现的是一个本地 coding agent harness。它围绕大模型做了一层可控的 runtime：每一轮先采集 workspace 状态和历史上下文，组装 prompt，然后让模型在受限工具集中选择动作；工具执行结果会写回 session history、working memory、checkpoint 和 trace，下一轮模型再基于最新上下文继续决策。整个系统强调可恢复、可审计、可测试和安全边界。

## 2. 整体架构

核心模块在 `bingo-main/bingo` 下：

- `cli.py`：命令行入口，解析参数，构建 agent。
- `runtime.py`：Agent 主循环，整个系统的核心控制器。
- `context_manager.py`：prompt 组装与上下文预算控制。
- `memory.py`：工作记忆、文件摘要、相关记忆召回、长期记忆。
- `workspace.py`：仓库快照、git 状态、项目文档摘要。
- `tools.py`：工具注册、参数校验、工具执行。
- `models.py`：不同模型后端适配层。
- `task_state.py`：单次任务状态机。
- `run_store.py`：运行产物落盘。
- `evaluator.py`：benchmark harness。
- `metrics.py`：实验指标、消融实验、报告生成。

整体数据流：

```text
CLI
 ↓
Bingo Runtime
 ↓
ContextManager  ← WorkspaceContext
 ↓               ← LayeredMemory
ModelClient
 ↓
parse model output
 ↓
Tool Registry
 ↓
Workspace / Shell / File operations
 ↓
History + Memory + Checkpoint + Trace + Report
```

## 3. 启动流程

项目从 `cli.py` 的 `build_agent()` 开始。

启动时主要做几件事：

1. 构建 `WorkspaceContext`
   读取当前目录、git root、分支、git status、最近 commit，以及 `README.md`、`pyproject.toml`、`package.json`、`AGENTS.md` 这类项目文档。

2. 加载 `.env`
   模型 API key、base url、model name 通过环境变量配置。

3. 构建模型客户端
   支持 Ollama、OpenAI-compatible、Anthropic-compatible、DeepSeek。

4. 创建或恢复 session
   如果传了 `--resume`，从 `.bingo/sessions/<session_id>.json` 恢复；否则新建 session。

5. 初始化 `Bingo`
   初始化 memory、tools、prefix、context manager、resume state。

面试说法：

> CLI 层只负责把外部参数翻译成 runtime 需要的对象，真正的 agent 状态都集中在 `Bingo` 实例里，包括 workspace、session、memory、tools、model client 和 context manager。

## 4. Bingo Runtime 主循环

项目最核心的是 `runtime.py` 的 `Bingo.ask()`。

它是一个 ReAct 风格循环：

```text
用户输入
 ↓
记录到 session history
 ↓
构建 prompt
 ↓
请求模型
 ↓
解析模型输出
 ↓
如果是 tool call：执行工具、记录结果、更新记忆、创建 checkpoint
 ↓
如果是 final answer：结束任务、写 report
```

模型每次只能输出两种东西：

```xml
<tool>...</tool>
```

或者：

```xml
<final>...</final>
```

这样 runtime 可以可靠判断下一步是执行工具还是返回最终答案。

面试可以这样讲：

> 我没有让模型自由输出自然语言后由人猜，而是定义了一个很小的协议：模型每轮只能返回一个工具调用或者最终答案。runtime 会解析模型输出，然后进入工具执行、安全校验、结果回写这一套确定性流程。

## 5. 上下文处理

上下文处理主要在 `context_manager.py`。

每轮真正发给模型的 prompt 由五部分组成：

```text
prefix
memory
relevant_memory
history
current_request
```

五部分分别是：

- `prefix`：稳定前缀，包括 agent 身份、规则、工具列表、工具调用格式、workspace 快照。
- `memory`：轻量工作记忆，比如当前任务摘要、最近访问文件、文件摘要、episodic note 数量、durable memory topic。
- `relevant_memory`：根据当前用户请求，从短期笔记和长期记忆里召回最相关的 3 条。
- `history`：会话历史，但不是无限塞入，会对旧历史做压缩。
- `current_request`：当前用户请求，永远放在最后，而且不会裁剪。

面试重点：

> 这个项目不是简单拼接聊天记录，而是把上下文分层管理。稳定信息、工作记忆、相关召回、历史记录和当前请求分别有自己的预算和裁剪策略，这样可以让 prompt 可控、可解释，也方便做实验和审计。

## 6. 上下文预算与压缩策略

默认总预算：

```python
DEFAULT_TOTAL_BUDGET = 12000
```

默认分区预算：

```text
prefix: 3600
memory: 1600
relevant_memory: 1200
history: 5200
```

如果超预算，会按这个顺序压缩：

```text
relevant_memory -> history -> memory -> prefix
```

但 `current_request` 不裁剪。

面试说法：

> 我们优先保留当前请求和 agent 规则，因为这是本轮决策最关键的部分。旧的相关记忆和历史可以被压缩，但当前请求必须完整保留，避免模型误解用户最新意图。

历史压缩策略：

- 最近 6 条历史尽量保留。
- 老的重复 `read_file` 会折叠。
- 老的文件读取可以复用 `file_summary`。
- 老的 `run_shell` 只保留前三行摘要。

所以它不是粗暴截断，而是带语义的压缩。

## 7. Memory 设计

记忆系统在 `memory.py`。

默认 memory 结构：

```python
{
    "working": {
        "task_summary": "",
        "recent_files": [],
    },
    "episodic_notes": [],
    "file_summaries": {},
    "task": "",
    "files": [],
    "notes": [],
    "next_note_index": 0,
}
```

可以分成四层：

1. Working memory
   当前任务摘要、最近文件。

2. Episodic notes
   每次读文件或工具异常后沉淀的小笔记。

3. File summaries
   文件摘要，并带 freshness hash，防止文件变了摘要还被误用。

4. Durable memory
   长期记忆，保存在 `.bingo/memory` 下，比如项目约定、关键决策、依赖事实、用户偏好。

读文件之后，`update_memory_after_tool()` 会做：

```text
remember_file
set_file_summary
append_note
```

写文件或 patch 文件后，会让旧摘要失效：

```text
invalidate_file_summary
```

面试说法：

> Memory 不是完整聊天记录，而是从工具结果中提炼出来的高价值状态。比如文件读过之后会保存摘要和 freshness hash；如果文件被修改，摘要会失效，避免使用过期上下文。

## 8. Relevant Memory 召回

相关记忆没有使用向量数据库，而是用简单透明的 token overlap：

```text
用户问题 tokenize
note 的 text/source/tags tokenize
按 tag 命中、关键词重叠、时间、note_index 排序
取前 3 条
```

同时会混合 durable memory。

面试说法：

> 这里没有上复杂 embedding，而是用了一个轻量、可解释的关键词召回机制。优点是简单、可测试、结果可解释，适合本地 coding agent 的 MVP 阶段。

## 9. Workspace Context

`workspace.py` 负责构建仓库快照。

它采集：

```text
cwd
repo_root
branch
default_branch
git status
recent commits
project docs snippets
```

并生成 `fingerprint()`。这个 fingerprint 用来判断 workspace 是否发生变化。如果分支、status、文档摘要等变化，prefix 可能需要刷新。

面试说法：

> WorkspaceContext 提供的是 agent 的“仓库第一印象”。它不会一开始读取整个仓库，而是只采集少量高价值信息，避免 prompt 爆炸。

## 10. 工具系统与安全边界

工具定义在 `tools.py`。

基础工具包括：

```text
list_files
read_file
search
run_shell
write_file
patch_file
delegate
```

工具分成安全和高风险：

- 读类工具：`list_files`、`read_file`、`search`，不需要审批。
- 写类/执行类工具：`run_shell`、`write_file`、`patch_file`，标记为 risky，需要 approval policy 控制。

工具执行不是模型直接调用函数，而是经过 runtime 的 `run_tool()`，里面有完整护栏：

```text
工具是否存在
参数是否合法
路径是否越界
是否重复调用
是否需要审批
执行前后 workspace snapshot
记录 affected paths / diff summary
更新 memory
写 trace
```

路径安全通过 `path()` 方法实现，用 `commonpath` 防止 `../` 跳出 workspace。

`patch_file` 也很保守，要求 `old_text` 在文件中恰好出现一次，避免误改多个位置。

面试说法：

> 模型不能直接碰文件系统，它只能请求工具调用。真正执行前 runtime 会做路径约束、参数校验、审批策略和重复调用检测。这样把模型的不确定性隔离在一个受控工具层里。

## 11. Delegate 子 Agent

`delegate` 是一个只读子 agent。

它会新建一个 `Bingo`，并设置：

```python
approval_policy="never"
read_only=True
depth=agent.depth + 1
max_steps 更小
```

用途是让子 agent 做受限调查，而不是执行危险操作。

面试说法：

> delegate 是一个受限子任务机制。它继承父 agent 的 workspace 和 model client，但是 read-only，而且有 depth 和 max_steps 限制，避免递归失控。

## 12. 模型适配层

`models.py` 把不同 provider 抹平成统一接口：

```python
complete(prompt, max_new_tokens, ...)
```

支持：

- `OllamaModelClient`
- `OpenAICompatibleModelClient`
- `AnthropicCompatibleModelClient`
- `FakeModelClient`

`FakeModelClient` 用在测试和 benchmark，保证 deterministic。

OpenAI-compatible 还支持 prompt cache：

```text
如果 backend 支持 prompt cache，就用 prefix hash 作为 prompt_cache_key
```

面试说法：

> runtime 不关心 HTTP 细节，只依赖统一的 `complete()`。不同 provider 的 endpoint、payload、usage metadata、SSE/JSON 差异都封装在 models.py 里。

## 13. Prompt Cache 设计

`Bingo.build_prefix()` 会生成 `prefix_state`：

```python
PromptPrefix(
    text,
    hash,
    workspace_fingerprint,
    tool_signature,
    built_at
)
```

每次 build prompt 时，会检查 workspace 或 tools 是否变化。稳定 prefix 的 hash 可以作为 cache key。

面试说法：

> prompt cache 的 key 不是整个 prompt 的 hash，而是稳定 prefix 的 hash。因为 history 和 current request 每轮都会变，如果对整段 prompt 缓存命中率会很差。这里缓存的是相对稳定的前缀部分。

## 14. Session、Run、Trace、Report 的区别

`SessionStore` 保存可恢复状态：

```text
.bingo/sessions/<session_id>.json
```

里面有：

```text
history
memory
checkpoints
resume_state
runtime_identity
```

`RunStore` 保存单次运行审计产物：

```text
.bingo/runs/<run_id>/task_state.json
.bingo/runs/<run_id>/trace.jsonl
.bingo/runs/<run_id>/report.json
```

区别：

```text
session：为了恢复对话
run：为了复盘单次任务
trace：过程事件流
report：最终摘要
task_state：当前任务状态快照
```

`trace.jsonl` 是追加写，适合记录事件序列：

```text
run_started
prompt_built
model_requested
model_parsed
tool_executed
checkpoint_created
run_finished
```

`report.json` 更像最终汇总，包括最终答案、tool steps、prompt metadata、memory promotion 等。

## 15. TaskState 状态机

`task_state.py` 记录单次 `ask()` 的状态。

字段包括：

```text
run_id
task_id
user_request
status
tool_steps
attempts
last_tool
stop_reason
final_answer
checkpoint_id
resume_status
```

状态包括：

```text
running
completed
stopped
failed
```

停止原因包括：

```text
final_answer_returned
step_limit_reached
retry_limit_reached
model_error
tool_timeout
approval_denied
```

面试说法：

> TaskState 让每次运行有明确生命周期。它区分 status 和 stop_reason，status 表示结果状态，stop_reason 表示为什么停下来，这样 benchmark 和 report 可以更精确分析失败原因。

## 16. Checkpoint 与恢复机制

checkpoint 保存：

```text
checkpoint_id
parent_checkpoint_id
schema_version
current_goal
completed
current_blocker
next_step
key_files
freshness
runtime_identity
summary
```

关键点是 `key_files` 会保存文件 hash。

恢复时 `evaluate_resume_state()` 会判断：

1. checkpoint schema 是否匹配。
2. key files 是否 stale。
3. runtime identity 是否变化。

恢复状态包括：

```text
no-checkpoint
full-valid
partial-stale
workspace-mismatch
schema-mismatch
```

然后 `render_checkpoint_text()` 会把恢复信息塞进下一轮 prompt。

面试说法：

> 恢复不是简单加载历史，而是带一致性检查。它会检查关键文件 hash 和 runtime identity，避免在文件已经变化、工具签名变化、模型参数变化时盲目继续。

## 17. 安全与隐私设计

项目里有几层安全设计：

1. workspace path 限制
   所有文件路径都必须在 repo root 下。

2. risky tool 审批
   `run_shell`、`write_file`、`patch_file` 需要 approval policy。

3. read_only 模式
   delegate 子 agent 和某些模式下禁止写操作。

4. shell 环境变量 allowlist
   `run_shell` 不继承完整环境，而是只传允许的环境变量，避免 secret 泄露。

5. secret redaction
   runtime 会识别 API key、token、password 等敏感字段，并在 trace/report 中打码。

6. 重复工具调用检测
   防止模型卡住连续重复调用同一个工具。

面试说法：

> 对 coding agent 来说，安全边界非常重要。这个项目的思路是模型只表达意图，runtime 负责执行控制。所有危险行为都经过工具层、审批层、路径层和审计层。

## 18. Benchmark 与评测体系

`evaluator.py` 提供 benchmark harness。

它会：

1. 读取 `benchmarks/coding_tasks.json`。
2. 复制 fixture repo 到临时目录。
3. 用 `FakeModelClient` 跑确定性任务。
4. 执行 agent。
5. 检查产物是否存在。
6. 跑 verifier。
7. 统计是否 within budget、verifier 是否通过、stop reason 是否正常。
8. 输出 benchmark artifact。

面试说法：

> 我没有只靠人工试用验证，而是做了一个 deterministic benchmark harness，用 scripted model output 固定模型行为，从而测试 runtime、工具、上下文、恢复机制这些确定性部分。

## 19. Metrics 与消融实验

`metrics.py` 做了更多实验：

- context ablation
  比较开启/关闭 context reduction 后 prompt 大小、当前请求是否保留。

- memory ablation
  比较 memory on/off/irrelevant 时是否需要重复读文件。

- recovery ablation
  比较 resume enabled/disabled 对恢复成功率、stale detection、workspace drift detection 的影响。

- run artifact aggregation
  从 `.bingo/runs` 里聚合 tool status、stop reason、prompt chars、cache hit rate 等。

面试说法：

> 这个项目不是只实现功能，还围绕 agent runtime 做了实验评估。通过消融实验可以证明 memory、context reduction、resume recovery 分别带来的收益。

## 20. 测试覆盖点

测试目录在 `tests/`。

主要覆盖：

- context manager section 顺序、压缩策略、当前请求保留。
- memory 的文件摘要、stale invalidation、retrieval。
- run store 的原子写入和 artifact。
- task state 的状态转换。
- evaluator 的 benchmark schema 和执行结果。
- safety invariants，比如路径逃逸、重复工具调用、审批拒绝。
- bingo 主流程，比如 tool call、final answer、checkpoint、trace。

面试说法：

> 测试重点不是模型能力，而是 runtime contract。因为模型输出不可控，所以我用 FakeModelClient 固定输出，验证工具协议、上下文拼装、状态落盘和安全约束这些确定性逻辑。

## 21. 项目技术亮点总结

可以总结成 8 个亮点：

1. Agent runtime 控制循环
   实现了 `prompt -> model -> parse -> tool -> record -> next prompt` 的闭环。

2. 结构化工具协议
   模型输出必须是 `<tool>` 或 `<final>`，降低解析不确定性。

3. 分层上下文管理
   prefix、memory、relevant memory、history、current request 分区预算管理。

4. 上下文压缩策略
   优先保留当前请求和稳定规则，对旧历史做语义压缩。

5. 轻量记忆系统
   working memory、episodic notes、file summaries、durable memory。

6. 可恢复 checkpoint
   基于 key file freshness 和 runtime identity 判断恢复状态。

7. 安全工具层
   路径限制、审批策略、只读模式、secret redaction、重复调用检测。

8. 可审计与可评测
   session/run/trace/report 分离，并配套 benchmark、metrics、ablation。

## 22. 面试完整讲解稿

下面这段可以直接背熟，然后按面试官追问展开。

> 这个项目是一个本地 coding agent，目标是让大模型可以在本地代码仓库里安全、可控、可恢复地完成工程任务。整体上我把它拆成 CLI、runtime、context manager、memory、workspace、tools、model adapter、run store 和 evaluator 几层。
>
> CLI 层负责解析参数、选择模型 provider、加载 `.env`、构建 workspace 快照，然后创建或恢复一个 Bingo agent。真正的核心在 runtime，也就是 `Bingo.ask()`。每次用户输入进来之后，runtime 会先把用户消息写入 session history，然后通过 ContextManager 重新构建 prompt。模型返回后，runtime 会解析它是工具调用还是最终答案。如果是工具调用，就经过工具校验、安全审批、路径约束后执行，再把结果写回 history、memory、trace 和 checkpoint，继续下一轮；如果是 final，就结束任务并写 report。
>
> 上下文处理是这个项目的重点。它不是简单把聊天记录全塞给模型，而是分成 prefix、memory、relevant memory、history、current request 五块。prefix 是稳定规则和工具说明，memory 是压缩后的工作状态，relevant memory 是根据当前问题召回的相关笔记，history 是会话历史，current request 是当前用户请求。ContextManager 给每个部分设置预算，如果超出总预算，会按 relevant memory、history、memory、prefix 的顺序压缩，但当前请求永远不裁剪。
>
> Memory 这一层也做了分层。读文件后会记录 recent files、生成 file summary，并追加 episodic note；写文件或 patch 文件后会让旧 summary 失效。每个 file summary 还带文件 hash，也就是 freshness，用来防止文件变化后继续使用过期摘要。长期记忆则落到 `.bingo/memory` 下，用 topic 管理项目约定、关键决策、依赖事实和用户偏好。
>
> 工具系统是安全边界。模型不能直接操作文件系统，只能请求 `list_files`、`read_file`、`search`、`run_shell`、`write_file`、`patch_file`、`delegate` 这些注册工具。runtime 在执行前会检查工具是否存在、参数是否合法、路径是否逃逸 workspace、是否重复调用、是否需要审批。对于写文件、shell 这类 risky tool，还会根据 approval policy 控制。执行后还会记录 affected paths、diff summary，并写入 trace。
>
> 恢复机制通过 checkpoint 实现。每次关键节点都会创建 checkpoint，保存当前目标、下一步、阻塞点、关键文件和文件 freshness。恢复 session 时，系统会检查 checkpoint schema、关键文件 hash、runtime identity，比如 cwd、model、tool signature、workspace fingerprint 是否变化。如果文件变了就是 partial-stale，如果 workspace 或 runtime 变了就是 workspace-mismatch，然后把这些恢复状态写进下一轮 prompt，避免 agent 盲目继续。
>
> 模型层通过 adapter 把 Ollama、OpenAI-compatible、Anthropic-compatible、DeepSeek 统一成 `complete(prompt, max_new_tokens)` 接口。OpenAI-compatible 还支持 prompt cache，用稳定 prefix 的 hash 作为 cache key，而不是用整个 prompt，因为 history 和当前请求每轮都会变。
>
> 最后，项目还有 benchmark 和 metrics。benchmark 使用 FakeModelClient 固定模型输出，在 fixture repo 里跑确定性任务，用 verifier 检查产物；metrics 做 context、memory、recovery 的消融实验，证明这些模块对 prompt 压缩、减少重复读取、恢复成功率有实际作用。整体上这个项目关注的不是单纯模型能力，而是如何把大模型包装成一个工程上可控、可审计、可测试的本地 coding agent。

## 23. 常见面试问题

### Q1: 这个项目和普通 ChatGPT 调 API 有什么区别？

普通调用 API 只是输入 prompt、拿输出。这个项目在模型外面实现了完整 runtime，包括工具协议、上下文预算、工作记忆、工具安全校验、session 恢复、运行审计和 benchmark。模型只是决策器，真正的工程控制在 runtime。

### Q2: 为什么不用完整历史？

完整历史会导致 prompt 过长，而且旧信息可能噪声很大。所以项目把上下文分层：近期历史保留，旧历史压缩，文件内容沉淀成摘要，相关笔记通过检索召回。这样既保留连续性，又控制 token 成本。

### Q3: 为什么不用向量数据库？

这个项目定位是轻量本地 agent，MVP 阶段优先可解释和可测试。所以 relevant memory 用 tag/关键词/时间排序。后续可以替换成 embedding retrieval，但当前方案足够透明，也方便 benchmark。

### Q4: 怎么保证模型不会乱改文件？

模型不能直接改文件，只能发工具调用。写文件和 shell 都是 risky tool，需要 approval policy。所有路径都经过 workspace root 校验，`patch_file` 要求 old_text 精确命中一次，执行前后还会 snapshot workspace 并记录 diff summary。

### Q5: checkpoint 有什么用？

checkpoint 用来恢复任务现场。它不只是保存一句摘要，还保存当前目标、下一步、关键文件和 hash。恢复时会检查文件是否 stale、runtime 是否 mismatch，避免在上下文已经失效时继续执行。

### Q6: 你怎么测试 agent？模型输出不是不稳定吗？

项目把 runtime 和模型能力解耦，用 FakeModelClient 固定输出，这样可以确定性测试工具协议、上下文拼装、安全边界、状态落盘和恢复机制。benchmark 也是基于 scripted outputs 跑 fixture repo 和 verifier。

## 24. 最核心的一条主线

面试前最后背这条线：

```text
CLI 构建 Bingo
 ↓
WorkspaceContext 采集仓库现场
 ↓
Bingo 初始化 session / memory / tools / prefix
 ↓
用户 ask
 ↓
ContextManager 拼 prompt
 ↓
ModelClient 返回 tool 或 final
 ↓
runtime parse
 ↓
run_tool 执行受控工具
 ↓
history + memory + checkpoint + trace 回写
 ↓
下一轮继续
 ↓
final 后写 report
```

只要能围绕这条线展开，再补上“上下文分层、安全工具层、checkpoint 恢复、benchmark 评测”，这个项目就能讲得完整而且工程味很足。
