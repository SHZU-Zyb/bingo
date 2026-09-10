# Bingo 真实仓库验证报告

验证规模：**1 个真实仓库**，55 源文件，16.7 K LoC。

仓库必须记录固定 commit；无法获得 Git commit 时使用完整内容 SHA-256 快照。源文件与 LoC 排除依赖、构建产物、生成缓存和 Bingo 运行目录。

## 端到端 Agent 结果

- 共执行 **2** 个任务，完成 **2** 个，任务完成率 **100.0%**。
- 平均 LLM 调用 **2.00 次/任务**，平均耗时 **1.02 s**，P95 **1.14 s**。
- 模型：`scripted-smoke`；重复次数：`1`。

## 检索消融

- Vector：Recall@5 `0.636`，MRR `0.386`，P95 `85.22 ms`，无答案准确率 `0.000`。
- Hybrid：Recall@5 `0.636`，MRR `0.386`，P95 `81.24 ms`，无答案准确率 `0.000`。
- Auto：Recall@5 `0.636`，MRR `0.386`，P95 `83.71 ms`，无答案准确率 `0.000`。
- Hybrid 相比纯 Vector：Recall@5 提升 **0.00 个百分点**，MRR 相对提升 **0.00%**，P95 降低 **4.67%**。

## 证据与口径

- 任务成功只由 verifier、产物存在性、正常停止原因和步骤预算共同判定，模型不能自行宣布成功。
- P95 使用每条查询预热一次后的重复测量；索引耗时与查询耗时分开记录。
- 跨仓库同时报告 micro 与等权 macro 指标，避免大仓库支配准确率。
- 变异修复任务、历史真实 issue 和合成干扰数据必须分别标注，不能混写成真实线上任务。
- 原始 JSON、Verifier stdout/stderr、隔离工作区和 Agent Trace 全部保留，可回溯每个结论。

## 工件

- 端到端原始 JSON：`<project-root>\artifacts\real-repository-e2e-scripted-smoke.json`
- 检索原始 JSON：`<project-root>\artifacts\real-repository-retrieval-fallback-diagnostic.json`

只有真实模型、固定仓库 revision、确定性 verifier 和足够任务量同时满足时，端到端结果才适合写入简历。小样本 smoke 结果只用于证明评测链路能够运行。
