# pdf-extract

用**确定性的本地** [MinerU](https://github.com/opendatalab/MinerU) 流水线，把本地 PDF
转成**清洗后的 Markdown**。

- 全程在本机运行，不上传、不依赖联网服务。
- Agent 从不读取 PDF 或 MinerU 的原始输出：只传入路径，回报产物路径与裁剪统计。
- 每篇 PDF 的唯一持久产物：`<name>_md/cleaned.md` + `<name>_md/image/`。
- 同时提供 agent **skill**（pi 与 Codex）与纯 **CLI**，有没有 agent 都能用。

[English](README.md)

## 安装

### 前置

- Linux（含 WSL Ubuntu）。
- Python `>=3.10,<3.15`（推荐 3.12）。
- MinerU 4.x（`mineru>=4.0,<5`）及其模型。
- 可选：`pypdf`（仅用于采样 PDF 文本层做自动路由）。
- Linux 工具 `ss` 与 `/proc`（batch 模式共享 API server 时使用）。

### 方案 A —— 一键安装（推荐）

```bash
git clone https://github.com/YZM-novice/pdf-extract.git
cd pdf-extract
./install.sh
```

`install.sh` 会：

1. 找一个合适的 Python，并**优先复用已有的 MinerU**（`PATH` 上的 `mineru-kit`、
   conda 环境，或 `$PDF_EXTRACT_MINERU_PYTHON`）；都没有才新建专用 venv
   `~/.local/share/pdf-extract/venv` 并安装 `mineru>=4.0,<5`（有 `uv` 用 `uv`，
   否则 `python -m venv` + `pip`）；
2. **先询问**再下载 MinerU 模型（约 2GB+）；
3. 为 pi 和/或 Codex 安装 skill、`pdf-extract` 终端入口、pi 扩展，以及
   （Codex）shell hook；
4. 把解析出的绝对路径固化进 skill 旁的 `pdf-extract.env`，无需 activate 环境、
   无需手工配置。

常用参数：`--pi`、`--codex`、`--all`、`--skills-only`、`--no-codex-hooks`、
`--no-models`、`--yes`。

### 方案 B —— 自备 MinerU

自己按官方说明装好 MinerU 4.x，然后只装 skill：

```bash
# 照官方 MinerU Quick Start
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -U "mineru>=4.0,<5"
mineru-kit parse document.pdf -o document.md --tier flash --ocr-mode txt

# 安装 pdf-extract skill + CLI
cd /path/to/pdf-extract
./install.sh --skills-only
```

首次走模型的解析可能下载权重。`--tier flash --ocr-mode txt` 只读文本层、不加载
推理模型；扫描件用 `--ocr-mode ocr`。

### MinerU 基本用法

```bash
mineru-kit parse document.pdf -o document.md --tier standard
mineru-kit parse document.pdf -o document.md --tier flash --ocr-mode txt
mineru-kit models download      # 下载/校验模型
```

### 卸载

删除安装产物即可：

```bash
rm -rf ~/.agents/skills/pdf-extract ~/.codex/skills/pdf-extract
rm -f  ~/.local/bin/pdf-extract ~/.pi/agent/extensions/mineru-pdf-extract.ts
# Codex hook：请手动编辑或删除 ~/.codex/hooks.json 中的条目
rm -f ~/.codex/hooks/block-pdf-reads.sh
```

## 使用

### 配合 agent（隐式触发）

skill **隐式触发**：只要让 agent 把 PDF 转成 Markdown，它就会自动使用。手动入口
仍然可用：

- pi：`/skill:pdf-extract`
- Codex：`$pdf-extract`

转换前 skill 会检查 `<pdf>_md/cleaned.md` 是否已存在并直接复用。因此成功转换过一次
的收益是永久的，跨会话也命中缓存。

### CLI

```bash
pdf-extract paper.pdf                      # 在 PDF 旁生成 paper_md/
pdf-extract a.pdf b.pdf --out ./out        # 批量，统一输出根
pdf-extract paper.pdf --tier flash --ocr-mode txt   # 快速验证
pdf-extract paper.pdf --name 2             # 容器 2_md/
```

选项：`--out`、`--name`（batch 时每个 PDF 一个，按输入顺序）、`--tier`
（`auto|flash|standard`）、`--ocr-mode`（`auto|txt|ocr`）、`--json`、`--force`、
`--timeout`、`--server-port`。

### 输出结构

```
paper_md/
├── cleaned.md     # 清洗后的 Markdown（唯一持久产物）
└── image/         # 外部图片，相对链接 ![](image/xxx.jpg)
```

只有容器（`<name>_md/`）可命名；容器内部名字固定。`image/` 只包含 `cleaned.md`
实际引用的图——MinerU 多写的公式/表格渲染会被丢弃。失败时临时目录**保留并上报**；
成功时删除。

### 改回"仅显式触发"

隐式触发是新用户的默认体验。若要求必须显式调用：

- pi：在安装后的 `~/.agents/skills/pdf-extract/SKILL.md` frontmatter 里加回
  `disable-model-invocation: true`。
- Codex：在 `~/.codex/skills/pdf-extract/agents/openai.yaml` 里设
  `policy.allow_implicit_invocation: false`。

## pi 与 Codex 的能力差异

| 能力 | pi | Codex |
|---|---|---|
| 读取拦截 | 扩展拦 `read` / `grep` / `bash` | **仅 shell 命令**（hook）；自带文件读取工具挡不住 |
| 手动入口 | `/skill:pdf-extract` | `$pdf-extract` |
| 工具 | `pdf-extract` 工具 | shell CLI（`pdf-extract`） |

**如实说明局限：** Codex 的 `PreToolUse` / `PostToolUse` 当前只对 shell 命令生效，
且从配置层加载（`~/.codex/hooks.json`，需 `[features].codex_hooks = true`），不从
skill 加载。因此 Codex **无法被完全阻止**使用自带文件读取工具。防御来自 skill 规则
（"不读 PDF"、"复用 `cleaned.md`"）+ shell hook；一旦产物存在，缓存的 `cleaned.md`
让源 PDF 不再需要被读取。

## 许可证

MIT，见 [LICENSE](LICENSE)。
