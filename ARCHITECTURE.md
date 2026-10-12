# pdf-extract — 架构

> 项目现状地图：边界、数据流、接缝、契约、改哪。**一页，开工必读。**
> 只写**契约**（别人能依赖的承诺），不写**机制**（内部实现手段）。
> 为什么这样选 → `docs/adr/`；术语 → `CONTEXT.md`；改了什么 → git。
> 读取清单（什么必读、什么按需）→ `AGENTS.md`。

## 1. 目的与范围

把本地 PDF 经本机 MinerU 转成**清洗后的 Markdown**。确定性流水线，AI 全程不读
PDF / raw md。

**非目标**：不把文件内容带回上下文；不做 PDF 编辑/合并/拆分；不提供联网或多用户
服务；不追求"清洗彻底"，只清理**确定的**出版商样板。

## 2. 外部上下文与运行模型

- 宿主：Linux（含 WSL Ubuntu）；Python `>=3.10,<3.15`；MinerU 4.x。
- 外部依赖：`mineru-kit`（`-f zip`）、MinerU 模型目录、`pypdf`（文本层采样，可选）。
- 运行形态：CLI `pdf-extract <pdf...>`；pi 扩展工具 `pdf-extract`（子进程调脚本）；
  Codex 侧 shell 命令（同一脚本）；skill `pdf-extract`（操作规程，pi / Codex 共用）。
- 安装形态：`./install.sh` 一键装（发现或新建 MinerU venv、下载模型、装 skill /
  终端入口 / pi 扩展 / Codex hook），解析出的绝对路径固化进安装产物
  （`pdf-extract.env`、终端入口、扩展读取配置），用户无需 activate 环境。
- 进程模型：单 PDF 直接跑 `mineru-kit`；batch 且含 `standard` 时，起**一个**共享
  `mineru-kit api-server`（loopback 固定端口），所有 PDF 经 `--remote-url` 复用，
  退出即按进程组杀掉。

## 3. 模块地图

| 部件 | 职责 |
|---|---|
| `scripts/pdf_extract.py` | 内核 + CLI：清洗、转换、路由、编排 |
| `scripts/mineru_server.py` | 共享 api-server 的启动/复用/归属/终止 |
| `extensions/mineru-pdf-extract.ts` | pi 工具 + 读取拦截状态机 |
| `hooks/codex/block-pdf-reads.sh` | Codex shell hook：拦 `.pdf` / 临时目录 |
| `agents/openai.yaml` | Codex skill UI 元数据（允许隐式触发） |
| `install.sh` | 引导安装器（MinerU + 双 harness + 固化路径） |
| `SKILL.md` | 给 agent 的操作规程（pi / Codex 共用） |

依赖方向单向：pi extension / Codex hook →（子进程）`pdf_extract.py` →
`mineru_server.py`；skill 描述如何用前两者。无反向依赖。

## 4. 数据流

**成功**：PDF → runner（`mineru-kit parse -f zip`，本地或 `--remote-url`）→ 临时
`out.zip` → 解包 `markdown.md` + `images/` → `clean()` →
`_copy_referenced_images()`（只落 md 实际引用的图，丢弃 MinerU 多写的公式/表格渲染）
→ `_rewrite_image_links()` → `<container>_md/cleaned.md` + `<container>_md/image/`。

路由在转换之前：`scan_text()` → `TextSignals` → `route_tier()` → `Routing`；
`--tier` 强制时跳过自动路由。

**失败**：任意阶段抛 `PdfExtractError` → 临时目录**保留**并上报；**仅成功才删**。

## 5. 接缝与扩展点

- `clean(markdown) -> (text, stats)` —— **纯函数**，无外部依赖，毫秒级迭代清洗规则。
- `_referenced_images()` / `_copy_referenced_images()` —— 图片选择缝：只复制 `cleaned.md`
  引用到的图；MinerU 在 `images/` 里多写的 `page_*_equation_*` / `page_*_table_*`
  渲染是孤儿文件，不落盘。
- `convert(pdf, out_dir, *, tier, ocr_mode, timeout, runner=)` —— 集成缝；`runner`
  可注入，测试无需模型。
- `route_tier(signals) -> Routing` —— 决策缝，可独立测。
- `MineruApiServer` —— 进程缝；`start()` / `stop()`，pid 文件记录归属。
- `extensions/` 读取拦截 —— 策略缝。

## 6. 契约与不变量

1. **接口**：`clean()` 对外签名稳定；`convert()` 的 `runner` 可注入。
2. **数据/产物**：只命名**容器** `<container>_md/`；容器内部固定
   `cleaned.md` + `image/`；图片以**相对**链接 `![](image/...)`；`image/` 内容 =
   `cleaned.md` 实际引用的图，**公式/表格不作为图片产物**。
3. **行为**：`clean()` **幂等**；**fail-safe**（无锚点不裁；未匹配则逐字节原样返回）；
   **protected 行**（代码围栏、`<details>`）对结构解析和**所有规则一律不透明**。
4. **失败/错误**：错误类型 → 退出码（arg=1, conversion=2, write=3, timeout=4）；
   临时目录成功删、失败留并上报。
5. **触发**：skill **隐式触发**（用户涉及 PDF→Markdown 时自动使用），不再要求显式
   调用；手动入口 pi `/skill:pdf-extract`、Codex `$pdf-extract`。用户可自行改回显式-only。
6. **路径解析**：`mineru-kit` = 固化配置（`pdf-extract.env`）→ `PDF_EXTRACT_MINERU_BIN`
   → `which` → 报错；解释器 = `PDF_EXTRACT_PYTHON` → 固化配置 → `mineru-kit` 同环境
   推导 → `python3`。仓库**不内置任何绝对路径**。
7. **拦截边界（诚实）**：pi 可拦 `read`/`grep`/`bash`；Codex 的 `PreToolUse` 只对 shell
   生效，**挡不住 Codex 自带文件读取工具**，只能靠 skill 规则 + shell 层防御。
8. **外部/CLI**：`--out/--name/--tier/--ocr-mode/--json/--server-port/--force/--timeout`；
   `--name` 必须是纯 basename，batch 时数量必须等于 PDF 数；`--json` 输出
   `{results, errors, any_low_confidence, exit_code}`。
9. **生命周期/并发**：batch `standard` 共享一个 server；SIGTERM/SIGINT 也要停 server；
   只复用**由 pid 文件证明归属且健康**的 server，否则拒绝。

## 7. 横切约定

- **错误**：统一 `PdfExtractError` 子类 + `exit_code`；意外异常归为 `ConversionError`。
- **报告**：人类模式只打印路径 + trim stats + 路由原因 + `low_confidence`；
  **绝不回吐内容**。JSON 模式同理。
- **配置**：`pdf-extract.env` 固化配置（`MINERU_BIN` / `MINERU_PYTHON`，安装时写）；
  `PDF_EXTRACT_CONFIG` 覆盖配置路径；`PDF_EXTRACT_MINERU_BIN`（脚本逃生口）；
  `PDF_EXTRACT_PYTHON` / `PDF_EXTRACT_SCRIPT`（扩展）；`XDG_CACHE_HOME`（pid 文件位置）。
- **生命周期**：临时目录前缀 `TEMP_PREFIX`；扩展为每个任务建 `TMPDIR` 以便围栏。

## 8. 约束与禁区

- 技术：需一个装了 MinerU（`mineru-kit`）的 Python `>=3.10,<3.15` 环境（conda 或
  `install.sh` 建的 venv 均可）；依赖 `ss` 与 `/proc`（Linux）。
- 平台：Linux / WSL；loopback 端口默认 18765。
- 组织：仓库是源码；`install.sh` 把产物拷到全局（`~/.agents/skills/pdf-extract/`、
  `~/.codex/skills/pdf-extract/`、`~/.pi/agent/extensions/`、`~/.local/bin`、
  `~/.codex/hooks.json`）。**测试必须用环境变量把安装目标指向临时目录**，勿写真实
  `$HOME`；改完仓库需重跑 `install.sh`。
- 禁区：不读 PDF / raw md / 临时目录（扩展拦截）；失败时也不要打开临时目录。

## 9. 设计优先级（项目性格，**不是**对 AI 的决策授权）

**前提（必须同时满足，不可交易）**：① 不把文件内容带进上下文（项目身份）
② fail-safe（不确定就不动，宁可少清洗不可删正文）③ 确定性（核心纯规则，模型只在边缘）。

**优化（在前提下按序取舍）**：④ 低 token / 低成本　⑤ 可测性　⑥ 功能丰富。

**决策权**：以上只用于**判断改动是否与项目冲突**。AI **不得**据此在价值之间自行
取舍。一旦改动 (a) 触及前提 ①–③，或 (b) 需要在 ④–⑥ 之间做**实质取舍**，**必须停下
说明冲突，交由设计者决定**。其余情况正常推进，无需打扰。

## 10. Where to change what

| 想改 | 去哪 | 配套测试 |
|---|---|---|
| 清洗规则 | `clean()` 的对应 pass | `tests/test_clean.py` |
| 结构解析（title / references / protected） | `_analyze_structure()`、`_Structure` | `tests/test_clean.py` |
| 输出命名 / 布局 | `_plan_targets()`、`convert()` | `tests/test_batch.py`、`tests/test_convert.py` |
| 路由阈值 / 策略 | `route_tier()`、`resolve_routing()` | `tests/test_routing.py` |
| api-server 生命周期 / 复用 | `scripts/mineru_server.py` | `tests/test_server.py` |
| CLI 参数 / 退出码 / JSON | `_build_parser()`、`main()`、`_json_*` | `tests/test_cli.py` |
| pi 工具参数 / 读取拦截 | `extensions/mineru-pdf-extract.ts` | `tests/js/mineru-pdf-extract.test.mjs`、`tests/test_extension.py` |
| skill 规程 | `SKILL.md` | `tests/test_skill.py` |
| 安装器 / 路径解析 | `install.sh`、`resolve_mineru_bin()`、`resolve_python()` | `tests/test_skill.py`、`tests/test_paths.py` |
| Codex hook / skill 元数据 | `hooks/codex/block-pdf-reads.sh`、`agents/openai.yaml` | `tests/test_codex.py` |

## 11. 测试与快速验证

- unittest：`tests/test_*.py`；node：`tests/js/*.mjs`。
- 端到端：`tests/test_e2e.py`。
- **涉及模型时用 `--tier flash --ocr-mode txt`** 做快速验证。

## 12. 指针

- 术语：`CONTEXT.md`
- 决策：`docs/adr/`（含 0003：隐式触发 / 双 harness / Codex 拦截边界）
- 安装与使用：`README.md`、`README.zh-CN.md`
- 原始规格（冻结）：`.scratch/pdf-extract/spec.md`
- 操作规程：`SKILL.md`
- 工单：`.scratch/pdf-extract/issues/`、`docs/agents/issue-tracker.md`
- 变更历史：git
