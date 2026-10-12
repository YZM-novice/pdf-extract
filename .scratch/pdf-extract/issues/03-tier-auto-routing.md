# 03: 档位自动路由 + 低置信度

**What to build:** 默认 `--tier auto` 依据 PDF 内容自动选档——无文本层（扫描件）→ `flash + ocr`；有文本层且启发式检测到公式 → `standard + txt`；有文本层、无公式 → `flash + txt`。**必须打印判档理由**（例如"检测到 N 处公式 → 使用 standard"）供人工兜底。`--tier` 可人工强制重跑。低置信度时在机器可读输出里给出 `low_confidence` 标记，供上层询问用户。

**Blocked by:** 01.

**Status:** ready-for-agent

- [x] 扫描件自动 `flash + ocr`，产出含正文文本
- [x] 含公式的文本 PDF 自动 `standard`（或标记 `low_confidence`）
- [x] 纯文本自动 `flash + txt`
- [x] 打印判档理由
- [x] `--tier` 强制指定时按指定档位执行
- [x] 低置信度时机器可读输出含 `low_confidence`

## Comments

- 2026-10-01: 实现完成。新增纯路由接缝 `scan_text(text) / route_tier(signals) -> Routing`、PDF 文本提取适配器 `extract_pdf_text(pdf)` 与 `resolve_routing(pdf, tier, ocr_mode)`；CLI 默认改为 `--tier auto`/`--ocr-mode auto`，新增 `--json`。测试见 `tests/test_routing.py`，全套 56 个通过（含 `PDF_EXTRACT_E2E=1`）。
- 启发式（可脱离 MinerU 快验）：数学符号数+密度（≥20 个且 ≥0.5%）、独立公式编号行（≥2）判 strong → `standard+txt`；弱信号（≥5 符号 / 单个编号 / ≥3 行尾编号引用）→ `flash+txt` + `low_confidence: true`；文本层少于 100 非空白字符视为扫描件 → `flash+ocr`（0 字符为确信，极稀疏为低置信）；提取器不可用 → `flash+auto` + 低置信。
- 真实冒烟：公式论文（5 页采样 297 数学符号/2 公式号）→ 自动 `standard+txt`（强制 standard 实跑 2 页 18.5s 成功）；纯文本 → `flash+txt`；光栅化扫描件 → 自动 `flash+ocr` 且 OCR 找回正文；`--json` 的 `low_confidence` 与 `any_low_confidence` 均正确；`--tier` 强制时跳过检测并打印 forced 理由。
- 按规格 D8/工单 02 的约定，`low_confidence` 只做机器可读标记 + 理由打印，由 AI/skill 负责向用户发问；脚本不交互阻塞。
