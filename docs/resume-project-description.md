# Bingo 简历项目说明

## 可直接放入简历的版本

### Bingo——面向代码仓库的本地智能 Coding Agent

**项目简介：** 独立设计并实现可在真实代码仓库中持续工作的本地 Coding Agent，覆盖代码检索、上下文管理、工具执行、会话恢复、Skill 扩展和多 Agent 协作；兼容 Ollama、OpenAI、Anthropic 与 DeepSeek 接口，并通过权限边界、运行工件和自动化测试保证执行过程可控、可恢复、可审计。

**技术栈：** Python、SQLite FTS5/BM25、AST、FastEmbed/ONNX、USearch HNSW、RRF、Concurrent Futures、Pytest

**核心工作：**

- 设计自适应代码检索链路，根据仓库规模、查询类型、路径范围和上下文预算，在直接加载、Symbol、BM25、向量及 Symbol Graph 间动态路由；基于 AST 建立类/函数/方法及调用关系，只对有界 `identity/behavior` 短卡生成向量，通过加权 RRF 融合并以文件哈希校验源码位置，降低长代码正文对向量匹配的干扰。
- 重构 Agent 上下文与记忆体系，将长期决策记忆、当前代码候选、源码证据和文件读取缓存分层管理；采用 48,000 字符分区预算、完整块装配、SHA-256 新鲜度校验和 LRU 淘汰，减少重复读取与过期代码污染，同时支持 Checkpoint 恢复。
- 实现基于 `SKILL.md` 的可扩展 Skill 系统，完成受限元数据发现、显式/alias/相关性路由、模型二次选择、正文延迟加载、附属资源按需读取和哈希复用；Skill 工具白名单只能收窄 Runtime 权限，使新增工作流无需修改 Agent 主循环。
- 构建有门控的多 Agent Workflow：普通任务由父 Agent 完成，搜索、执行和测试解析优先走本地算法；仅对 2～4 个独立跨模块调查并发创建只读子 Agent，对多失败、跨模块或非结构化错误按需创建诊断 Agent。完整日志无损落盘，父 Agent只接收 4,000 字符以内的结构化报告并可按行回读证据，有效控制主上下文膨胀。
- 完成工程化执行边界，包括工作区路径隔离、危险工具审批、子 Agent能力白名单、敏感环境变量脱敏、Session/Trace/Report 持久化、重复调用保护和失败降级。当前全量回归为 **218 passed、2 skipped、0 failed**；可复现的 8-query fixture 消融集上，Auto/Hybrid 的 **Recall@5 与 MRR 均为 1.0**，观测 P95 分别为 **12.31 ms/12.35 ms**。

## 更短的三条版本

如果简历版面有限，可使用下面三条：

- 独立开发面向真实代码仓库的本地 Coding Agent，兼容 4 类模型接口，具备受控工具执行、Session/Checkpoint 恢复、Trace/Report 审计及敏感信息脱敏能力。
- 构建 AST Symbol Index、FTS5/BM25、短卡向量、Symbol Graph 与加权 RRF 融合的自适应检索，根据仓库规模和证据质量按需升级 RAG；8-query 固定消融集上 Auto/Hybrid Recall@5、MRR 均为 1.0，P95 为 12.31/12.35 ms。
- 实现 `SKILL.md` 延迟加载与有门控的多 Agent Workflow，优先使用父 Agent和本地算法，仅在独立跨模块调查或复杂诊断时启动只读子 Agent；全量自动化测试 **218 passed、2 skipped、0 failed**。

## 面试时的数据口径

- 检索指标来自仓库内可复现的 8-query 集成消融集，覆盖精确标识符、英文语义、中文跨语言、调用关系和负样本；它用于证明各检索通道与路由链路可运行，不宣称代表所有生产仓库精度。
- 当前项目索引验证曾生成 209 个 Python Symbol 和 418 张短向量卡，向量覆盖率 100%，正文不进入 embedding；对应查询会先返回位置卡，再通过 `read_symbol` 做哈希校验和精确源码读取。
- 小/中/大仓库路由按 Chunk 数划分为 `≤200`、`201～5000`、`>5000`，大仓库可选 HNSW ANN；仓库内保留 213 Chunk 与 5013 Chunk 的规模化测试工件。
- `218 passed、2 skipped` 是 2026-09-10 在项目虚拟环境中的完整测试结果。跳过项来自可选能力，不是功能失败。

## 一句话介绍

> Bingo 是一个强调检索质量、上下文成本和执行边界的本地 Coding Agent：它会根据任务选择父 Agent、本地算法或受限子 Agent，并用可复现评测、完整运行工件和 218 项通过测试证明系统能够真正运行。
