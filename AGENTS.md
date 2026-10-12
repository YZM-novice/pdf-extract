# AGENTS.md

## 读取清单（开工前先读）

`AGENTS.md` 每次会话都自动加载，所以这里只放**读取协议**，不放地图。

**必读（每次开工，不要跳过）**
1. `ARCHITECTURE.md` —— 项目现状：边界、数据流、接缝、契约、改哪。
2. `CONTEXT.md` —— 领域术语。

**相关时读（按任务触发）**
- `docs/adr/` —— 改动触及某决策区域时，读对应 ADR（为什么这样选）。
- `SKILL.md` —— 改 agent 用法 / 工具接口时。
- `.scratch/pdf-extract/spec.md` —— 需追溯原始意图时。

**按需检索（勿通读）**
- 源码：`rg` + 只读相关函数；对应关系见 `ARCHITECTURE.md` 第 10 块。
- 测试：只读对应测试文件。
- git：`log` / `blame` 只查相关文件。

不确定该不该读时，**宁可读，不要猜**。

## 文档分层（不要重复造）

| 工件 | 回答 | 谁维护 |
|---|---|---|
| git | 改了什么 | 自动 |
| `ARCHITECTURE.md` | 现在是什么样、改哪 | 边界/数据流/契约变化时就地更新 |
| `docs/adr/` | 为什么这样选、放弃了什么 | 决策时追加一条 |
| `CONTEXT.md` | 术语 | 词确定时更新 |
| 代码 + docstring | 符号是什么 | 随代码 |

- **不写变更日志**：git 已经记录改了什么，重复即冗余。
- **不写代码字典**：逐函数/逐变量说明是代码的镜像，必然腐烂，且错地图比没地图更糟。
  需要符号索引时用脚本（`ast`）**按需生成、不入库**。
- 改架构（边界/数据流/契约）就更新 `ARCHITECTURE.md`；难反悔的决策加一条 ADR。

## Agent skills

### Issue tracker

Issues and specs are tracked as local markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical triage roles; label strings equal their names. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
