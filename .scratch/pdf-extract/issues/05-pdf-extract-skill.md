# 05: Skill `pdf-extract`（AI 显式触发）

**What to build:** 提供一个全局 skill `pdf-extract`，**仅用户显式触发**（`disable-model-invocation`，由此获得确定性信号，不做意图识别）。它指示 AI：从用户消息取出一个或多个 PDF 路径；调用 `pdf-extract` 工具（若扩展可用），否则执行固定命令；**禁止读取 PDF、禁止读取 raw md 或临时目录**；只在最后汇报产物路径与裁剪统计；批量且用户未指定存储位置时先提醒/确认；脚本报低置信度时先问用户选 `standard` 还是 `flash`。以**拷贝**方式从本仓库安装到全局 skill 目录。

**Blocked by:** 01, 03.

**Status:** resolved

- [x] 全局安装 `pdf-extract` skill（拷贝式）
- [x] 仅显式触发，模型不自动唤起
- [x] 指示 AI 取路径 → 调用工具/命令 → 只汇报路径与统计
- [x] 指示 AI 不读 PDF、不读 raw md/临时目录
- [x] 批量未指定位置时先提醒/确认
- [x] 低置信度时先问用户选档

## Comments

- 2026-10-01: 实现完成。新增 `SKILL.md`（`name: pdf-extract`，`disable-model-invocation: true`，description 说明仅显式触发）+ `install.sh`（拷贝式安装到全局）+ `tests/test_skill.py`。全套 88 个测试通过（含 `PDF_EXTRACT_E2E=1` 真实 MinerU）。
- 仓库根即 skill 源：`SKILL.md` 与 `scripts/`；`install.sh` 把 `SKILL.md` 与 `scripts/pdf_extract.py`、`scripts/mineru_server.py` 拷到 `~/.agents/skills/pdf-extract/`，并写可执行终端入口 `~/.local/bin/pdf-extract`（指向 mineru 环境 python + 安装后的脚本）。无软链（§15.2）；安装目标可用 `PDF_EXTRACT_SKILL_DIR` / `PDF_EXTRACT_BIN_DIR` 覆盖，测试只写临时目录。
- SKILL.md 契约：取用户路径 → 有扩展则调 `pdf-extract` 工具，否则跑固定命令；禁止读 PDF/raw md/临时目录；只汇报产物路径 + 裁剪统计；批量且无 `--out` 先确认；`low_confidence` 先问选 `standard`/`flash`。测试对 frontmatter 与这些指令逐项断言。
- 已在真实全局位置执行 `./install.sh`，并用生成的小文本 PDF 走通终端入口：自动 `flash+txt`、裁 5 行、输出无 base64、临时目录即删、缺文件退出码 1。
