# Bingo 真实仓库验证报告

验证规模：**1 个真实仓库**，55 源文件，16.9 K LoC。

仓库必须记录固定 commit；无法获得 Git commit 时使用完整内容 SHA-256 快照。源文件与 LoC 排除依赖、构建产物、生成缓存和 Bingo 运行目录。

## 端到端 Agent 结果

- 共执行 **2** 个任务，完成 **2** 个，任务完成率 **100.0%**。
- 平均 LLM 调用 **2.00 次/任务**，平均耗时 **1.05 s**，P95 **1.09 s**。
- 模型：`scripted-smoke`；重复次数：`1`。
- **Harness smoke：本组使用确定性脚本输出，只验证评测链路，不是 LLM 能力成绩，不可写入简历。**

## 检索消融

- Vector：Recall@5 `0.909`，MRR `0.576`，P95 `358.56 ms`，无答案准确率 `1.000`。
- Hybrid：Recall@5 `0.909`，MRR `0.544`，P95 `387.69 ms`，无答案准确率 `1.000`。
- Auto：Recall@5 `0.727`，MRR `0.526`，P95 `384.09 ms`，无答案准确率 `1.000`。
- Hybrid 相比纯 Vector：Recall@5 变化 **+0.00 个百分点**，MRR 相对下降 **5.53%**，P95 上升 **8.12%**。

## 证据与口径

- 任务成功只由 verifier、产物存在性、正常停止原因和步骤预算共同判定，模型不能自行宣布成功。
- P95 使用每条查询预热一次后的重复测量；索引耗时与查询耗时分开记录。
- 跨仓库同时报告 micro 与等权 macro 指标，避免大仓库支配准确率。
- 变异修复任务、历史真实 issue 和合成干扰数据必须分别标注，不能混写成真实线上任务。
- 原始 JSON、Verifier stdout/stderr、隔离工作区和 Agent Trace 全部保留，可回溯每个结论。

## 工件

- 端到端原始 JSON：`<project-root>\artifacts\real-repository-e2e-scripted-smoke.json`
- 检索原始 JSON：`<project-root>\artifacts\real-repository-retrieval.json`

只有真实模型、固定仓库 revision、确定性 verifier 和足够任务量同时满足时，端到端结果才适合写入简历。小样本 smoke 结果只用于证明评测链路能够运行。

## 评测框架回归验证

- 项目虚拟环境全量测试：`218 passed, 2 skipped, 0 failed in 93.20s`。
- 新增真实仓库评测专项测试：`6 passed`。
- Evaluator/Retrieval 相关回归：`32 passed, 2 skipped`。
- Ruff 静态检查：通过。
- Python 字节码编译检查：通过。
- E2E 与检索实验源码 revision 均为 `snapshot:sha256:70d42758bb1f6bb785e6dada94e491f30e61954c4330ea47668a001cd66d72a1`，不存在跨版本比较。

## 当前结论与后续门槛

本轮已证明评测框架可在真实源码、真实本地向量模型和确定性 verifier 上运行，也如实发现 Hybrid 排序与延迟尚未优于纯 Vector。当前仅有 1 个仓库和 12 条检索查询，不能据此宣称跨仓库泛化能力；脚本化 E2E 的 100% 完成率也不能作为模型成绩。

建议达到以下规模后再提炼简历数字：至少覆盖小、中、大三档共 5 个以上固定 revision 的真实仓库，检索查询不少于 100 条，真实模型修复任务不少于 30 条且每条至少重复 3 次。届时报告应同时给出置信区间、失败类别和原始工件链接。
