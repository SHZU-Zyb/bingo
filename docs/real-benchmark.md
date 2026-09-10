# Bingo 真实仓库评测框架

## 目标

这套评测回答两类问题：Bingo 在真实代码仓库中能否完成有确定验收条件的任务；Hybrid/Auto 检索相对纯 Vector 是否带来可量化收益。所有结果同时保留机器可读 JSON、Verifier 日志、Agent Trace、隔离工作区和面向简历的 Markdown 报告。

评测不会把以下结果混为一谈：

- `scripted-smoke`：用确定性模型输出验证 Harness，本身不代表 LLM 能力；
- `mutation-repair`：在真实仓库快照中注入已知缺陷，用测试验证修复能力；
- `historical-issue`：基于真实历史 issue/commit 构造的任务；
- `synthetic-distractor`：只用于检索规模压力测试；
- `real-repository retrieval`：查询来自真实仓库并标注实际源码位置。

## 文件

| 文件 | 作用 |
|---|---|
| `benchmarks/real-repositories.json` | 仓库、revision、端到端任务和查询集入口 |
| `benchmarks/real_retrieval/bingo.json` | Bingo 仓库的真实代码检索标注 |
| `bingo/real_benchmark.py` | 清单校验、规模统计、E2E 和检索消融、报告渲染 |
| `bingo/real_benchmark_cli.py` | `bingo-benchmark` 命令入口 |
| `artifacts/real-repository-*.json` | 原始、可重复统计的评测结果 |
| `docs/metrics/real-repository-validation.md` | 自动生成的验证报告 |

## 仓库清单

每个仓库必须有唯一 ID 和本地路径。建议填写完整 commit SHA；没有 Git 元数据时，框架会对排除项之外的完整内容生成 SHA-256 快照。

```json
{
  "id": "project-a",
  "path": "D:/benchmarks/project-a",
  "expected_revision": "0123456789abcdef...",
  "queries_file": "real_retrieval/project-a.json",
  "exclude": ["benchmarks", "generated"]
}
```

如果当前 revision 与 `expected_revision` 不一致，运行直接失败，不会在漂移后的源码上继续产生不可比较的数据。

规模统计只计算已知编程语言后缀的物理行数，同时报告源文件数、总文件数、字节数和分语言分布。`.git`、`.bingo`、虚拟环境、依赖、构建产物、缓存、工件和 `.env` 默认排除。

## 端到端任务

任务在完整仓库的隔离副本上执行。原仓库不会被修改。

```json
{
  "id": "repair-parser",
  "repo_id": "project-a",
  "category": "mutation-repair",
  "prompt": "修复解析器并运行相关测试",
  "allowed_tools": ["read_file", "search", "run_shell", "patch_file"],
  "step_budget": 8,
  "timeout_seconds": 120,
  "setup_replacements": [
    {
      "path": "src/parser.py",
      "old_text": "正确源码",
      "new_text": "注入缺陷"
    }
  ],
  "expected_paths": ["src/parser.py"],
  "verifier": "{python} -m pytest tests/test_parser.py -q"
}
```

成功条件是：

```text
verifier exit code == 0
AND expected_paths 全部存在
AND Agent 正常返回 final
AND 父 Agent 工具步数不超过预算
```

模型自己的“已经完成”不参与判分。每项任务记录：

- 完成状态和失败阶段；
- 父/子/总 LLM 调用次数；
- 工具调用次数；
- Agent 耗时、端到端耗时和 P95；
- Provider 返回时的输入/输出 Token 与覆盖率；
- 策略拒绝次数和安全事件；
- Verifier stdout/stderr 工件；
- 完整 Agent Session、Trace 与 Report。

完成率同时提供 Wilson 95% 区间，并按仓库和任务类型拆分，避免少量简单任务掩盖复杂任务失败。

## 检索查询

每条查询必须给出实际目标文件；`paths=[]` 表示无答案查询。

```json
{
  "id": "symbol-extraction",
  "query": "parse Python AST into class function and method symbols",
  "kind": "semantic",
  "paths": ["src/symbol_index.py"]
}
```

查询定义文件必须从待索引副本中排除，否则检索器可能直接命中题目文本，造成数据泄漏。Bingo 清单已经排除 `benchmarks` 和 `docs`。

默认对 `vector`、`hybrid`、`auto` 做消融，Top-K 固定为 5。每个 mode 开始前清理查询向量缓存，每条查询先预热一次，再重复测量 5 次。索引时间独立报告。

指标包括：

- Recall@5；
- MRR；
- 无答案准确率；
- P50/P95 查询耗时；
- 等权仓库 macro 指标；
- 全部查询 micro 指标；
- Hybrid/Auto 相比 Vector 的百分点、相对提升和耗时变化。

## 命令

只统计仓库规模：

```powershell
python -m bingo.real_benchmark_cli inventory
```

运行不计入简历成绩的 Harness smoke：

```powershell
python -m bingo.real_benchmark_cli e2e --provider scripted
```

使用真实模型运行三次端到端任务：

```powershell
python -m bingo.real_benchmark_cli e2e --provider deepseek --repetitions 3
```

运行真实 FastEmbed 检索消融：

```powershell
.venv\Scripts\python.exe -m bingo.real_benchmark_cli retrieval --latency-repetitions 5
```

组合已有 JSON 并重新生成报告：

```powershell
python -m bingo.real_benchmark_cli report `
  --e2e-output artifacts/real-repository-e2e.json `
  --retrieval-output artifacts/real-repository-retrieval.json
```

安装项目后也可以使用 `bingo-benchmark`，参数相同。

## 简历采用门槛

建议满足以下条件后再把结果写成“真实仓库端到端验证”：

- 至少 5 个固定 revision 的真实仓库；
- 至少 30 个由确定性 verifier 验收的任务；
- 每个任务至少运行 3 次；
- 同时包含 mutation repair 与 historical issue，分别报告；
- Token 统计覆盖率足够，Provider/模型/温度保持一致；
- 检索集至少 100 条，且标注由第二人或脚本复核；
- 报告置信区间、失败案例和回退次数，不只选择成功指标。

当前仓库内置数据用于验证整条评测链路，并诚实展示 Hybrid 可能在 Recall 上提升、同时在 MRR 或延迟上退化的情况。这样的负结果同样有工程价值，可以直接指导 reranker、负样本门控和查询分类的下一轮优化。

## 2026-09-10 验证结果

- 评测专项测试：`6 passed`；
- 相关 Evaluator/Retrieval 回归：`32 passed, 2 skipped`；
- 全量项目回归：`218 passed, 2 skipped in 93.20s`；
- Ruff、Python 编译和 CLI help/inventory：通过；
- Harness smoke：1 个真实仓库快照、2 个变异修复任务，`2/2` 通过，平均 2 次脚本化模型轮次、0.98 秒；该数字只证明 Harness 可运行；
- 真实 FastEmbed 消融：55 个 Python 源文件、约 16.9 K LoC、854 个 Symbol、1,708 张短卡、向量覆盖率 100%、12 条查询；
- Vector：Recall@5 `0.909`、MRR `0.576`、P95 `280.65 ms`；
- Hybrid：Recall@5 `0.909`、MRR `0.544`、P95 `292.93 ms`；
- Auto：Recall@5 `0.727`、MRR `0.526`、P95 `289.40 ms`。

当前真实代码小样本没有证明 Hybrid 优于 Vector：Recall@5 持平，MRR 下降 5.53%，P95 上升 4.38%。这组结果已保留，不能替换成之前合成 fixture 上更好看的数字。DeepSeek 端到端尝试因当前执行环境无法访问外部接口而失败，单独保留为基础设施失败报告，不计作模型能力结果。
