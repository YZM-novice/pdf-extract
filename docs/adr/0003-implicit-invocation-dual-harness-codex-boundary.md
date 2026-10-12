# ADR-0003：隐式触发、双 harness、Codex 拦截边界

- 状态：接受
- 日期：2026-10-12
- 相关：`SKILL.md`、`extensions/mineru-pdf-extract.ts`、`hooks/codex/block-pdf-reads.sh`、`agents/openai.yaml`、`install.sh`

## 背景

初始设计（`.scratch/pdf-extract/spec.md`）要求 skill **只由显式信号触发**
（`disable-model-invocation: true`），以获得确定性、避免模型做意图识别的 token 与
不确定性。开源发布时，用户要求改为**隐式触发**：用户一旦涉及 PDF→Markdown 就应自动
使用本 skill，否则新用户不知道要敲 `/skill:pdf-extract`。

同时目标 harness 从 pi 扩展到 Codex。两者的**读取拦截能力不对等**：

- pi 扩展能拦 `read` / `grep` / `bash`；
- Codex 的 `PreToolUse` / `PostToolUse` 当前**只对 shell 命令生效**，挡不住 Codex
  自带的文件读取工具；hook 从配置层（`~/.codex/hooks.json`）加载，需
  `[features].codex_hooks = true`。

## 决策

1. **默认隐式触发**。`SKILL.md` 删除 `disable-model-invocation: true`，`description`
   写清"何时该用"；`agents/openai.yaml` 设 `policy.allow_implicit_invocation: true`。
   手动入口保留：pi `/skill:pdf-extract`，Codex `$pdf-extract`。用户若想改回显式-only，
   README 说明如何自行加回。
2. **保留 pi 扩展**（工具 + 读取拦截），仅 pi 可用；Codex 不需要也不加载它。
3. **Codex 侧只用 skill + shell hook**，并**如实说明能力边界**：README 与 skill 均写明
   Codex 挡不住自带读取工具，shell hook 只是尽力而为的防御。
4. **新增"复用已存在 `cleaned.md`"规则**：转换前先看 `<pdf>_md/cleaned.md` 是否存在，
   存在即复用。这使"某次误读 PDF"的代价不重复，跨会话也能命中缓存。

## 后果

- 新用户零配置即可命中 skill；代价是模型可能在不该用时也唤起，由 `description`
  与缓存规则降低风险。
- 拦截强度在 pi 与 Codex 之间**不相同**，文档必须诚实，不制造"完全阻止"的错觉。
- 缓存规则让产物成为**持久收益**：只要成功过一次，后续不再读源 PDF。

## 考虑过的替代方案

- **保持显式-only**：与原冻结 spec 一致，但新用户体验差，已否决。
- **为 Codex 也做读取拦截扩展**：Codex 目前没有等价扩展点，hook 只覆盖 shell；
  在能力不存在时承诺拦截是误导，已否决。
- **不做缓存检查**：每次重跑浪费模型算力，且误读风险重复，已否决。
