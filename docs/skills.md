# Bingo Skill 路由与按需加载

## 1. 功能定位

Skill 是可以独立扩展的 Agent 工作流程。它解决“面对这一类任务应该怎样做”的问题；自适应检索解决“为了完成任务应该读取哪些代码证据”的问题；Tool Registry 则限制模型最终能够执行哪些动作。

三者在 Bingo 中是串联关系：

```text
用户请求
  ↓
Skill Router：选择工作流程
  ↓
Context Router / Retrieval Router：选择信息源和代码证据
  ↓
Context Manager：按预算组装 Skill、记忆、检索结果、源码证据和历史
  ↓
Model Client
  ↓
受约束的工具调用
```

没有 Skill 目录、没有匹配结果或某个 Skill 无效时，Bingo 仍执行原来的通用 Agent 流程。Memory、Evidence Cache、自适应检索、Checkpoint、Trace 和 Report 都继续生效。

## 2. 目录约定

项目级 Skill 位于：

```text
.bingo/skills/
├── testing/
│   ├── SKILL.md
│   ├── references/
│   │   └── pytest.md
│   └── templates/
│       └── test-report.md
└── code-review/
    ├── SKILL.md
    └── checklists/
        └── default.md
```

本项目已经提供 `testing` 和 `code-review` 两个可运行示例。`.gitignore` 继续忽略 `.bingo/sessions`、`.bingo/runs`、检索索引等运行数据，只放行 `.bingo/skills/**`，因此 Skill 可以和源码一起版本管理。

普通仓库扫描仍然忽略 `.bingo`。`SkillRegistry` 使用独立入口显式扫描 `.bingo/skills`，避免运行状态目录进入代码检索语料。

## 3. SKILL.md 元数据格式

`SKILL.md` 由受限 frontmatter 和正文组成：

```markdown
---
name: testing
description: Design, run, diagnose, and report project tests
version: '1.0'
aliases:
  - test
  - pytest
triggers:
  - 测试
  - 回归验证
  - test failure
priority: 60
allowed_tools:
  - read_file
  - search
  - run_shell
resources:
  references:
    - references/pytest.md
  templates:
    - templates/test-report.md
---
# Testing Skill

1. Identify the smallest relevant test scope.
2. Reproduce a failure before changing production code.
3. Run the focused test and then the relevant regression suite.
```

字段含义：

| 字段 | 是否必需 | 用途与限制 |
|---|---|---|
| `name` | 是 | 全局唯一的小写名称，只允许数字、字母和连字符，最长 64 字符 |
| `description` | 是 | 路由摘要，最长 500 字符 |
| `version` | 否 | Skill 版本，默认 `1` |
| `aliases` | 否 | 显式选择别名，例如 `test` |
| `triggers` | 否 | 中英文语义触发短语 |
| `priority` | 否 | 同分时的确定性优先级，范围 0–100 |
| `allowed_tools` | 否 | 对基础工具集做收窄；空列表表示继承基础工具集 |
| `resources` | 否 | 允许按需读取的引用、模板或检查表清单 |

元数据解析器只接受该功能需要的 YAML 子集，不执行 YAML 标签或任意对象构造。资源只允许 `.md`、`.txt`、`.json`、`.yaml` 和 `.yml` 文本文件。

## 4. 三阶段加载

### 4.1 目录发现

`SkillRegistry.discover()` 只打开每个 `SKILL.md` 的 frontmatter。frontmatter 上限为 8,192 字符，最多发现 128 个 Skill。

注册结果只保存：

```text
name + description + aliases + triggers + priority
version + allowed_tools + resource manifest
metadata_hash + local paths
```

正文不进入注册表的 `routing_text`，附属资源也不会在这个阶段读取。这使 Skill 数量增长时，启动和路由上下文仍由短元数据控制。

### 4.2 正文加载

只有路由选中的 Skill 才由 `SkillLoader.load()` 读取完整正文。正文上限为 8,000 字符，整个 `SKILL.md` 上限为 16,384 字符。

Loader 为完整文件生成 SHA-256：

- 哈希相同：复用进程内 `LoadedSkill`，不重复解析。
- 哈希变化：重新读取正文，并清空该版本对应的资源哈希缓存。
- 正文为空或超限：本轮不启用该 Skill，并在 Session 中记录加载错误。

### 4.3 资源按需读取

Skill 正文只告诉模型有哪些声明资源。模型需要详细资料时调用：

```xml
<tool>{"name":"read_skill_resource","args":{"skill_name":"testing","resource_path":"references/pytest.md","max_chars":4000}}</tool>
```

单次最多返回 4,000 字符，资源原文件最多 65,536 字符。返回值包含 Skill 名、相对路径、内容哈希、正文和截断标记。只有当前 Active Skill 的已声明资源可以读取；未激活的 Skill、未声明路径和越界路径都会被拒绝。

## 5. 路由优先级

### 5.1 用户显式指定

支持以下形式：

```text
$testing 运行相关测试
/skill testing 运行相关测试
请用$testing检查这次改动
```

Skill 名称和 alias 都可使用。显式选择的优先级最高，一轮最多选择两个 Skill。

显式名称不存在时不会悄悄换成另一个 Skill。路由结果为 `explicit_missing`，Prompt 会包含缺失名称、相似名称建议和继续使用通用流程的说明。

### 5.2 Router 元数据语义匹配

没有显式选择时，`SkillRouter` 只使用以下短文本：

```text
name + description + aliases + triggers
```

当前实现综合计算：

- alias 的完整词命中；
- trigger 短语命中；
- 英文词项与中文双字词的元数据覆盖率；
- Skill priority，仅用于同分排序。

得分达到阈值时选择 Top-1。得分不足时返回 `fallback`，不加载任何正文。

### 5.3 模型按语义选择

每轮 Prompt 都包含一个独立、受预算限制的 Skill Catalog，其中只有 `name + description + aliases + triggers`，没有任何 Skill 正文。即使 Router 没有高置信度结果，模型也知道当前项目实际提供了哪些工作流。

模型判断某个工作流与任务相关时调用：

```xml
<tool>{"name":"activate_skill","args":{"skill_name":"code-review"}}</tool>
```

Runtime 在下一次模型调用前加载并注入正文。如果所有 Skill 都不相关，模型不调用 `activate_skill`，直接使用通用 Agent 流程。`list_skills` 保留为主动刷新和查看完整注册结果的工具，但不是模型选择 Skill 的必经步骤。

因此实际链路是：

```text
显式指定 → 直接加载
没有显式指定 → Router 高置信度匹配 → 自动加载
Router 未匹配 → 模型查看 Skill Catalog → 相关则 activate_skill
模型判断都不相关 → 不激活任何 Skill，继续通用 Agent
```

## 6. Session 状态

Session 把“读过”和“本轮生效”分开：

```json
{
  "skills": {
    "registry_fingerprint": "sha256...",
    "loaded": {
      "testing": {
        "version": "1.0",
        "metadata_hash": "sha256...",
        "content_hash": "sha256...",
        "loaded_resources": {
          "references/pytest.md": "sha256..."
        }
      }
    },
    "active": [
      {
        "name": "testing",
        "mode": "explicit",
        "reason": "user explicitly selected skill",
        "score": 1.0,
        "content_hash": "sha256..."
      }
    ],
    "diagnostics": [],
    "load_errors": []
  }
}
```

- `loaded` 是跨轮缓存与审计记录，只保存版本、哈希和资源引用，不保存正文。
- `active` 是当前顶层用户请求的工作流集合。
- 同一次请求的多个工具步骤复用同一组 Active Skills。
- 任务结束后清空 `active`；下一条用户请求重新路由。

这避免了上一轮测试规则意外影响下一轮架构解释任务。

## 7. Context Manager 接入

Prompt 顺序现在是：

```text
prefix
skill catalog
skills
working memory
relevant memory
retrieval candidates
source evidence
history
current request
```

未开启自动检索时，检索候选和源码证据区块为空。Skill Catalog 位于稳定前缀之后，让模型知道可选能力；Active Skill 区块紧随其后，描述当前任务已经启用的执行流程。

默认总预算仍为 48,000 字符。Skill Catalog 上限为 2,000 字符，按完整元数据卡片装入，并且在超预算时优先缩减；Active Skill 区块上限为 8,000 字符，按完整块装入，预算不足时整个区块省略并写入元数据，不会从中间裁断规则。当前用户请求保持完整。

每次 `prompt_built` 的元数据包含：

```text
skill_route.mode
skill_route.selected
skill_route.missing
skill_route.scores
skill_route.active
skill_route.catalog_rendered_chars
skill_route.rendered_chars
sections.skill_catalog.raw_chars
sections.skill_catalog.rendered_chars
sections.skills.raw_chars
sections.skills.rendered_chars
```

`run_started` Trace 同样记录路由方式、选中项、缺失项和得分，便于回答“为什么加载这个 Skill”。

## 8. 工具权限边界

Skill 的 `allowed_tools` 只能缩小 Runtime 已经注册的工具集合：

```text
effective tools = base tools ∩ every active Skill allowlist
```

它不能注册新工具、解除 `run_shell/write_file/patch_file` 的风险级别，也不能绕过审批策略。`list_skills`、`activate_skill` 和 `read_skill_resource` 是 Skill 管理工具，始终可用于完成按需加载闭环。

多个 Skill 同时启用时，普通工具必须被所有非空 allowlist 接受。这个交集策略防止一个 Skill 扩大另一个 Skill 的权限。

## 9. 失败与安全边界

| 情况 | 行为 |
|---|---|
| `.bingo/skills` 不存在 | 注册空目录，继续通用 Agent |
| 单个 frontmatter 无效 | 隔离该 Skill，写入 diagnostics |
| 声明名称重复 | 所有冲突项都不注册 |
| 显式名称不存在 | 给出建议，继续通用流程 |
| 语义得分不足 | `fallback`，不加载正文 |
| Skill 正文过长 | 拒绝加载，记录 load error |
| 资源未声明或不存在 | 工具返回明确错误 |
| `..`、绝对路径或不允许扩展名 | 注册或读取阶段拒绝 |
| Skill 目录/资源符号链接逃逸 | resolved path containment 检查拒绝 |
| 正文哈希变化 | 重新加载并更新 Session 哈希 |
| 工具不在 active allowlist | Runtime 在执行前拒绝 |

Skill 正文是项目提供的工作流指令，但其优先级低于 Runtime 的系统规则和工具安全边界。Reference 与 template 只是按需数据，不会改变工具审批。

## 10. 核心模块

| 模块 | 职责 |
|---|---|
| `bingo/skill_registry.py` | 目录发现、受限 frontmatter 解析、校验、诊断、元数据哈希 |
| `bingo/skill_router.py` | 显式语法、alias、trigger、词项语义评分、回退和建议 |
| `bingo/skill_loader.py` | 正文延迟加载、SHA-256 复用、资源白名单与路径边界 |
| `bingo/runtime.py` | 每请求路由、active 生命周期、模型动态激活、Trace/Session 接入 |
| `bingo/context_manager.py` | Skill 独立区块、8,000 字符预算、完整块装配 |
| `bingo/tools.py` | `list_skills`、`activate_skill`、`read_skill_resource` 与参数校验 |

## 11. 测试结果

验证日期：2026-09-09。

完整命令：

```powershell
python -m pytest -q
```

结果：

```text
218 passed, 2 skipped in 93.20s
```

4 个跳过项对应未安装的可选 `usearch` ANN 依赖，以及 Windows 环境下无法稳定创建符号链接或目录联接的测试条件。没有失败项。

Skill 专项测试覆盖：

- 注册阶段不把正文放入路由文本；
- 无效 frontmatter 和重复名称隔离；
- `$name`、`/skill alias` 与中文相邻语法；
- trigger/alias 元数据语义匹配与无匹配回退；
- 显式名称缺失和相似名称建议；
- 正文哈希复用与文件变化重载；
- 声明资源按需读取与路径越界拒绝；
- `loaded` 和 `active` 生命周期分离；
- 多工具步骤复用 Active Skill；
- 模型通过 `activate_skill` 动态选择；
- allowlist 在工具执行前收窄权限；
- Skill 上下文按完整块装入或省略；
- 原有 Agent、Context、Retrieval、Memory 和安全测试回归通过。

## 12. 面试讲法

可以用下面这段话概括：

> 我在 Agent Runtime 上增加了一套基于 SKILL.md 的可扩展工作流系统。启动和每轮路由阶段只扫描有大小上限的元数据，不读取正文；用户可以通过 `$skill` 显式指定，Router 可以根据 alias、trigger 和描述做相关性评分。每轮还有一个最多 2,000 字符的 Skill Catalog，因此 Router 未命中时，模型仍知道项目有哪些能力：相关就调用 `activate_skill`，不相关就继续通用流程。真正选中后才加载完整正文，references 和 templates 继续通过受控工具按需读取。Session 把 loaded cache 和 per-request active state 分开，正文只在当前任务中占用上下文；工具 allowlist 只能收窄基础权限。这样 Skill 决定 Agent 怎么工作，自适应检索决定读取什么代码，Context Manager 决定有限预算里最终保留哪些内容。

设计的核心不是“多拼一段 Prompt”，而是把发现、路由、加载、资源访问、生命周期、权限和可观测性拆成独立边界，因此添加新 Skill 不需要修改 Agent 主流程。
