# Context、Memory 与代码检索职责拆分设计

## 目标

解决文件摘要、相关记忆、历史工具结果和自适应代码检索重复进入 Prompt 的问题，同时保留跨轮任务恢复、长期决策召回和避免重复读取源码的能力。

## 设计结论

系统采用一个 Context Router 协调四类独立信息源：

1. Task State / Checkpoint 保存当前目标、进度、阻塞和证据引用。
2. Recalled Memory 只保存和召回跨任务仍有价值的偏好、约定、决策和已验证经验。
3. Adaptive Code Retrieval 从当前仓库生成临时候选位置卡。
4. Evidence Cache 按文件路径、行范围和内容哈希缓存已读取的源码；命中当前候选时直接形成 Source Evidence。

代码正文、文件摘要、Symbol 位置和普通读取结果不再进入长期 Memory。代码检索与 Memory 召回可以复用相似的检索算法，但必须使用独立语料、失效规则和可信度。

## Prompt 结构

最终顺序为：

1. Prefix 与 Checkpoint
2. Working State
3. Recalled Memory（路由命中时）
4. Retrieval Candidates（代码查询且启用自动检索时）
5. Source Evidence（候选命中有效缓存时）
6. Recent History
7. Current Request

当前请求永不裁剪。Source Evidence 以完整缓存块为单位装箱，超预算时丢弃整块，不截成无法定位的半段代码。候选和证据按 `path + range + file_hash` 去重。

## Context Router

Router 返回可解释的路由决策：

- 代码意图：启用代码检索。
- 历史决策、偏好、约定或恢复意图：启用 Memory 召回。
- 混合意图：两者都启用。
- 无明确 Memory 意图但存在词项相关的非代码记忆：允许低成本召回。
- 关闭 feature flag 时，对应数据源不得进入 Prompt。

路由元数据记录原因、启用的数据源、候选数、缓存命中数和失效数。

## Evidence Cache

缓存保存在 Session 中，采用 LRU 上限，条目包含：

- `path`
- `start_line`
- `end_line`
- `content`
- `summary`
- `file_hash`
- `symbol_id`（可选）
- `created_at`、`last_accessed_at`

读取文件或 Symbol 后更新缓存。写文件、Patch、文件哈希变化、文件删除或路径越界都会让相关条目失效。缓存正文有单条和总条目上限，避免 Session 无限增长。

旧 Session 的 `file_summaries` 保持可读取并继续做 freshness 清理，但不再渲染到 Memory，也不进入 Recalled Memory。它们没有原文，不能伪装成 Source Evidence。

## Memory 语义

Working State 只显示当前任务摘要和最近文件引用。Recalled Memory 允许 `durable`、人工或显式沉淀的 `episodic`、以及失败恢复所需的 `process` 笔记。来源指向仓库文件的旧读取摘要视为 code-derived note，召回时过滤。

中文和英文 Memory 查询都必须可检索。Memory 的结果是软先验；当前 Source Evidence 与文件哈希是代码事实来源。

## 历史压缩

旧的重复 `read_file` 结果继续折叠，但优先使用 Evidence Cache 的摘要。没有有效缓存时再使用兼容的旧文件摘要；两者都没有时使用普通工具摘要。最近窗口保留完整工具结果，避免破坏当前控制循环。

## 验收条件

- `read_file` 不再自动创建可召回的 episodic note。
- Memory Prompt 不再展开文件摘要正文。
- 代码查询不会因旧文件摘要产生 Recalled Memory 重复项。
- 相同文件和范围在哈希未变化时可作为 Source Evidence 复用。
- 文件变化后缓存不会进入 Prompt。
- 混合查询可同时得到历史决策和代码候选。
- feature flag、旧 Session、手动 `retrieve_code` 和现有工具协议保持兼容。
- trace/report 能解释各信息源的选择、预算与缓存命中。

