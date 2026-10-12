# 07: pi 扩展 —— 读取拦截

**What to build:** 在 `pdf-extract` 扩展上增加读取拦截，状态机 `idle → active(task) → idle`，**默认 idle，不拦任何东西**。`pdf-extract` 工具被调用的瞬间：登记本次输入的 PDF 与本次临时目录，进入 active。active 期间，对 `read` / `bash` / `grep` 且目标落在受保护集合的调用返回 block 并给出理由。工具**成功**返回（清洗 md 已产出、临时目录已删）→ 立即注销该任务；工具**失败/中止** → 保留登记直到本轮 `agent_end` 才注销；`agent_end` **无条件清空全部**登记。不保护清洗后的 md 与 `images/`；限制只在一次转换的生命周期内存在，绝不整会话常开，任务结束后读 PDF 能力自动恢复。

**Blocked by:** 06.

**Status:** resolved

- [x] 默认不拦截任何读取
- [x] 转换期间读取本次 PDF / raw md / 临时目录被 block 并给出理由
- [x] 工具成功后立即解除限制
- [x] 工具失败时限制保留到本轮 `agent_end`
- [x] `agent_end` 无条件清空全部登记
- [x] 转换结束后同一会话内可正常读其他 PDF
- [x] 清洗后的 md 与 `images/` 始终可读

## Comments

- 2026-10-01: 实现完成。全部在 `extensions/mineru-pdf-extract.ts`：状态机用模块级 `activeTasks: Set<ActiveTask>`，`tool_call` 处理器在集合非空时才工作，故默认 idle、不拦任何东西。
- 临时目录归属：扩展在工具执行前 `mkdtempSync` 一个父目录并作为 `TMPDIR` 传给脚本，于是「本次临时目录」在脚本存在之前就已知、可被拦截；脚本的 `mkdtemp` 子目录落在其内。
- 登记 `{输入 PDF 绝对路径, 临时父目录}`；`read`/`grep` 按解析后的绝对路径判定（含子路径），`bash` 按命令串匹配绝对路径、相对 cwd 形式与 PDF basename（尽力而为，符合规格已知缺口）。清洗后的 `<name>.md` 与 `images/` 不在保护集内，始终可读。
- 成功：`completeTask` 立即从 `activeTasks` 注销并删除临时父目录；失败/中止：不注销、保留目录，直到 `agent_end` 调 `clearRegistrations()` 无条件清空。`session_shutdown` 兜底删除保留的失败临时目录（幂等）。
- 测试：在 `tests/js/mineru-pdf-extract.test.mjs` 扩展 Node 行为测试（共 10 个）。用假脚本的 `hold` 模式精确制造「转换进行中」窗口，断言 PDF/raw md/临时目录的 read/grep/bash 被 block 且清洗 md/images 可读；断言成功后立即解除且临时目录被删；断言失败后仍拦截直到 `agent_end`。全套通过（含真实 MinerU E2E）；并用真实 `typebox`/pi 包直接 import 验证扩展可注册 `tool_call`/`agent_end`/`session_shutdown`。
