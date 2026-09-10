# Bingo 真实仓库验证报告

验证规模：**1 个真实仓库**，55 源文件，16.9 K LoC。

仓库必须记录固定 commit；无法获得 Git commit 时使用完整内容 SHA-256 快照。源文件与 LoC 排除依赖、构建产物、生成缓存和 Bingo 运行目录。

## 端到端 Agent 结果

- 共执行 **2** 个任务，完成 **0** 个，任务完成率 **0.0%**。
- 平均 LLM 调用 **1.00 次/任务**，平均耗时 **2.32 s**，P95 **2.34 s**。
- 模型：`deepseek`；重复次数：`1`。
- 失败分类：`agent_or_model_error=2`。
- **本轮所有任务均在 Agent/模型调用阶段失败，完成率不能用于评价模型编码能力。**

## 检索消融

- Vector：Recall@5 `0.909`，MRR `0.576`，P95 `280.65 ms`，无答案准确率 `1.000`。
- Hybrid：Recall@5 `0.909`，MRR `0.544`，P95 `292.93 ms`，无答案准确率 `1.000`。
- Auto：Recall@5 `0.727`，MRR `0.526`，P95 `289.40 ms`，无答案准确率 `1.000`。
- Hybrid 相比纯 Vector：Recall@5 变化 **+0.00 个百分点**，MRR 相对下降 **5.53%**，P95 上升 **4.38%**。

## 证据与口径

- 任务成功只由 verifier、产物存在性、正常停止原因和步骤预算共同判定，模型不能自行宣布成功。
- P95 使用每条查询预热一次后的重复测量；索引耗时与查询耗时分开记录。
- 跨仓库同时报告 micro 与等权 macro 指标，避免大仓库支配准确率。
- 变异修复任务、历史真实 issue 和合成干扰数据必须分别标注，不能混写成真实线上任务。
- 原始 JSON、Verifier stdout/stderr、隔离工作区和 Agent Trace 全部保留，可回溯每个结论。

## 工件

- 端到端原始 JSON：`<project-root>\artifacts\real-repository-e2e-deepseek.json`
- 检索原始 JSON：`<project-root>\artifacts\real-repository-retrieval.json`

只有真实模型、固定仓库 revision、确定性 verifier 和足够任务量同时满足时，端到端结果才适合写入简历。小样本 smoke 结果只用于证明评测链路能够运行。
