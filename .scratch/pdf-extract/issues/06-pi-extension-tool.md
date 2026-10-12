# 06: pi 扩展 —— `pdf-extract` 工具

**What to build:** 一个 pi 扩展，注册名为 `pdf-extract` 的工具：参数 `pdf_path`（必填）与可选 `tier` / `ocr_mode`。执行时调用转换脚本，**只返回**产物路径、图片目录、裁剪统计（以及低置信度标记），**绝不返回任何文件内容**。本质是"AI 替用户敲那条终端命令"，其余自动化都在脚本里。以**拷贝**方式从本仓库安装到全局扩展目录。

**Blocked by:** 01.

**Status:** resolved

- [x] 注册 `pdf-extract` 工具，参数 `pdf_path` 必填、`tier`/`ocr_mode` 可选
- [x] 调用转换脚本并只返回路径 + 统计
- [x] 从不返回文件内容
- [x] 拷贝式安装到全局扩展目录后可用

## Comments

- 2026-10-01: 实现完成。扩展源在 `extensions/mineru-pdf-extract.ts`；`install.sh` 新增拷贝到 `~/.pi/agent/extensions/`（目标可用 `PDF_EXTRACT_EXTENSION_DIR` 覆盖）；`tests/test_skill.py` 断言扩展被拷贝且非软链。
- 工具：`name: pdf-extract`，参数 `pdf_path`（必填）+ 可选 `tier`/`ocr_mode`。`execute()` 用 `spawn` 调脚本（默认 `~/.agents/skills/pdf-extract/scripts/pdf_extract.py` + mineru 环境 python，可用 `PDF_EXTRACT_SCRIPT`/`PDF_EXTRACT_PYTHON` 覆盖）加 `--json`，只把 `{产物路径, 图片目录, 裁剪统计, tier/理由, low_confidence}` 格式化进 `content`，结构化值放 `details`；从不读或回传文件内容。失败时抛错，携带脚本 error message 与保留的临时目录路径。
- 测试接缝：扩展只 import pi 专属包，故用 Node 内置 test runner 在 `tests/js/mineru-pdf-extract.test.mjs` 中把真实扩展文件拷进临时目录、旁置 `typebox`/`pi-coding-agent` 桩包后 import，并用假 python 脚本替掉 MinerU。覆盖：注册/schema、只回传路径+统计且不泄露 md 内容、`--tier`/`--ocr-mode` 透传、low-confidence 标记、失败抛错含临时目录、无 JSON 崩溃时回传 stderr。`tests/test_extension.py` 作 Python 桥把 Node 测试并入 `python -m unittest`。
- 全套通过（含真实 MinerU E2E 与 Node 行为测试）；已执行 `./install.sh`，`~/.pi/agent/extensions/mineru-pdf-extract.ts` 与仓库源逐字节一致。
