# 上下文交接：MinerU PDF → 清洗 Markdown（`pdf2md`）

> 本文档由 `/grill-me` 会话产出，**冻结全部已定案决策**，供后续会话直接实现。
> 状态：**需求冻结，待用户签字**。任何改动请在此文档留痕，不要只在对话里口头改。
> 用途：新会话的 Agent 只需读本文档即可实现，无需回溯原对话。
>
> ⚠️ **本文件是原始规格（冻结），描述当时的意图与决策，已被仓库根的
> `ARCHITECTURE.md` 取代为当前实现的入口。不要在此追加变更记录（git 负责）。**

---

## 0. 一句话定位

把「本地 MinerU 转换 + 规则清洗」封装成一个**确定性的脚本**，再配一个 **pdf-extract 的 skill**、一个**可选扩展（`pdf2md` 工具 + 读取拦截）**和一个**终端入口**，最终效果：

```text
用户给 PDF 路径 → AI 调 pdf2md 工具（= 替用户敲了那条终端命令）
            → 脚本内部跑 mineru + 清洗
            → 得到清洗后的 md（AI 全程不读 PDF、不读 raw md）
```

用户也可以**完全不经 AI**，自己在终端敲 `pdf2md <file>` 执行同一套流程。

---

## 1. 用户与环境（已核实事实）

| 项 | 事实 |
|---|---|
| 宿主 | Windows + WSL Ubuntu；Agent 跑在 WSL 里 |
| MinerU | **4.0.10**，Python **3.12.14**，conda 环境名 `mineru` |
| 可执行 | `<mineru-env>/bin/mineru-kit` |
| 模型目录 | `~/.mineru/models/` |
| 配置 | `~/.mineru/config.yaml`，`model.source: modelscope` |
| 模型状态 | `MinerU2.5-Pro-...-GGUF`（1.2G）**已就绪**；`MinerU-4_models_onnx`（792M）**已就绪** |
| 实测快档 | `--tier flash --ocr-mode txt`：12 页约 **6.5 秒**，基本不加载大模型 |
| 实测输出 | 默认 `markdown` 格式 = **1.95 MB**（48 张图全是 base64，纯文字仅 59.8 KB） |
| 关键发现 | `--format zip` 解包 = `markdown.md` + `images/*.jpg`（外部图片、相对链接），md 仅 **61.9 KB** —— 直接小 31 倍，**无 base64** |

> 结论：**用 `-f zip` 从源头绕开 base64**，不需要写「检查 base64 再剥离」的逻辑。

---

## 2. 核心原则（不可违背）

1. **机械工作本地脚本化，零 token**；AI 只做「传路径 / 汇报路径」，不读内容。
2. **禁止 AI 读 PDF 与 raw md**；允许读**清洗后的 md**（那是给未来「文献分析」用的）。
3. **raw md 只是中间产物**：放临时目录、用完即删，永不作为持久产物。
4. **唯一持久产物**：清洗后的 `<pdf名>.md` + `images/`。
5. **不做意图识别**：用「显式 skill + 工具调用」这类确定信号，避免 AI 理解的不确定与 token 消耗。
6. **清洗底线**：绝不损伤正文；**fail-safe**——正则定位不到锚点就不裁。
7. **图片用相对链接**，保留 `images/` 前缀（有效路径优先，标签反而更长更易让 AI 乱找）。

---

## 3. 冻结决策（逐条）

| # | 决策 |
|---|---|
| D1 | 交付形态：**脚本为核心**；pdf-extract skill；`pdf2md` 自定义工具；终端入口 |
| D2 | 取图：**`mineru-kit parse -f zip`**（外部图片、无 base64） |
| D3 | 图片引用：**保留 `![](images/xxx.jpg)` 原样**，不做标签/去前缀 |
| D4 | raw md：**不持久化**，仅临时目录，成功后删 |
| D5 | 清洗：裁 ①参考文献 ②期刊抬头/出版商页脚/DOI/copyright ③页眉页脚页码；**v1 不裁作者与单位**（容忍几行，避免误伤） |
| D6 | 档位路由：扫描件→`flash+ocr`；启发式检测到公式→`standard`；纯文本→`flash+txt`；默认自动 |
| D7 | 人工兜底：脚本打印判档理由；可用 `--tier` 重跑；低置信度时 AI 先问一句 |
| D8 | 批量：每篇 PDF 各自在**其所在目录**建 `<pdf名>_md/`；用户未指定存储位置时，AI 必须**先提醒/确认** |
| D9 | 批量 + standard：起一次 `api-server` 复用，跑完必杀（专用端口 + pid + finally） |
| D10 | AI 全程不读 PDF/raw md；脚本只返回路径 + 统计 |
| D11 | 拦截扩展：**做**；默认 OFF，以 `pdf2md` 工具调用为激活信号，生命周期见 §7 |
| D12 | 终端入口 `pdf2md <file>`：**做**（真·零 token，绕过 AI） |
| D13 | 脚本语言：**Python**，复用 `mineru` 环境 |
| D14 | skill 拆分：`pdf2md` 只做转换；未来「文献分析」另做一个 skill，读清洗 md |

---

## 4. 架构与文件布局

```text
~/.agents/skills/pdf2md/                 ← 集中一处（注意是复数 .agents）
├── SKILL.md                            ← pdf-extract skill
└── scripts/
    ├── pdf2md.py                       ← 内核：档位路由 + mineru + 清洗 + 图片
    └── clean_rules.py                  ← 清洗规则（可并入 pdf2md.py）

~/.pi/agent/extensions/
└── mineru-pdf2md.ts                    ← 扩展：pdf2md 工具 + 读取拦截

~/.local/bin/pdf2md                     ← 终端入口（包装脚本，调 pdf2md.py）
```

> 若 `~/.local/bin` 不在 PATH，需在 shell 配置里追加。

三入口共用**同一个** `pdf2md.py`，行为完全一致。

---

## 5. 脚本规格 `scripts/pdf2md.py`

### 5.1 CLI

```bash
pdf2md.py <pdf...> [--out DIR] [--tier auto|flash|standard]
                     [--ocr-mode auto|txt|ocr] [--force] [--json]
```

- `<pdf...>`：一个或多个 PDF（批量）。
- `--out DIR`：统一输出根；**省略时**默认每篇各自在 PDF 所在目录建 `<pdf名>_md/`。
- `--tier`：默认 `auto`（走 D6 路由）。
- `--ocr-mode`：默认 `auto`。
- `--force`：目标目录已存在时覆盖；默认遇到已存在则报错退出（防误删）。
- `--json`：输出机器可读结果（供扩展/自动化）。

### 5.2 档位路由（`--tier auto`）

| 判定 | 档位 |
|---|---|
| PDF 无文本层（扫描件） | `flash` + `ocr` |
| 有文本层且启发式检测到公式 | `standard` + `txt` |
| 有文本层、无公式 | `flash` + `txt` |

- 启发式：用 PDF 文本库统计数学字符 / 公式编号密度，超过阈值判为「有公式」。
- **必须打印决策理由**（例如：`检测到 18 处公式 → 使用 standard`），供人工兜底。
- 低置信度时，脚本以 `--json` 返回一个 `low_confidence: true` 字段，由 skill 让 AI 问用户一句。

### 5.3 转换命令（脚本内部实际调用）

```bash
<mineru-env>/bin/mineru-kit parse "<pdf>" \
    -o "<临时目录>/out.zip" -f zip --tier <tier> --ocr-mode <ocr>
```

> 注意：`-o` 在 zip 模式下是 `.zip` **文件路径**；批量时 `-o` 是目录（本脚本仍逐篇调用，不依赖 mineru 的批量）。

### 5.4 图片

- 直接采用 zip 解出的 `images/` 与相对链接，**不改写、不加标签**。

### 5.5 清洗规则

裁掉：
1. **参考文献**：命中 `References` / `Bibliography` 标题 → 直到文件末尾；
2. **期刊抬头/出版杂物**：`Contents lists available at ScienceDirect`、期刊名、`journal homepage`、DOI / ISSN / copyright 行；
3. **独立成行的页眉 / 页脚 / 页码**。

**不裁**：正文、标题下的作者与单位（v1 容忍）。

安全规则：
- fail-safe：定位不到锚点 → **不裁**；
- 规则只作用于行级，不跨正文结构删内容；
- 输出裁剪统计（行数 / 字符数）供核对。

### 5.6 输出结构与临时产物

```text
<pdf所在目录>/                （或 --out 指定的根）
├── <pdf名>.pdf
└── <pdf名>_md/
    ├── <pdf名>.md            ← 清洗后正文（唯一持久产物）
    └── images/
```

- 临时目录：`/tmp/mineru-pdf2md-<pid>-<ts>/`，内含 `out.zip` 与解包结果；
- **成功**：写出行产物后**立即删除**临时目录；
- **失败**：保留临时目录并把路径写进错误输出（供排查），由扩展的拦截兜底（§7）；下次成功或会话结束再清。

### 5.7 退出码与错误

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 参数/路径错误（PDF 不存在、目标已存在且未 `--force`） |
| 2 | mineru 转换失败 |
| 3 | 清洗/写盘失败 |
| 4 | 超时 |

错误信息必须包含：失败阶段、原始报错、临时目录路径。

---

## 6. Skill 规格 `~/.agents/skills/pdf2md/SKILL.md`

Frontmatter：

```yaml
---
name: pdf2md
description: 将本地 PDF 转成清洗后的 Markdown（调用本机 MinerU，无需读文件内容）。当用户提供 PDF 路径并要求转 Markdown/提取正文时使用。
disable-model-invocation: true
---
```

要点：

- **pdf-extract**：只有用户显式 `/skill:pdf2md` 才触发；由此获得确定信号。
- 指示 AI：
  1. 从用户消息取出 PDF 路径（可多个）；
  2. **调用 `pdf2md` 工具**（若扩展可用），否则执行固定命令 `pdf2md.py ...`；
  3. **禁止**读取 PDF、禁止读取 raw md/临时目录；
  4. 只在最后汇报：产物路径 + 裁剪统计；
  5. 若为批量且用户未指定存储位置，**先提醒/确认**（D8）；
  6. 若脚本报 `low_confidence`，先问用户选 `standard` 还是 `flash`。

---

## 7. 扩展规格 `~/.pi/agent/extensions/mineru-pdf2md.ts`

扩展同时提供两件事：**`pdf2md` 自定义工具** + **读取拦截**。

### 7.1 工具

- `name: "pdf2md"`，参数 `pdf_path`（必填）、可选 `tier`/`ocr_mode`。
- `execute()`：调用 `scripts/pdf2md.py`，**只返回** `{产物路径, 图片目录, 裁剪统计}`，绝不返回文件内容。
- 本质：**AI 替用户敲那条终端命令**；其余自动化都在脚本里。

### 7.2 拦截时机与周期（精确契约）

状态机：`idle → active(task) → idle`，**默认 idle（不拦任何东西）**。

| 阶段 | 行为 |
|---|---|
| 激活 | `pdf2md` 工具被调用瞬间：登记 `{本次输入 PDF 路径[], 本次临时目录}`，进入 active |
| active | `tool_call` 命中 `read`/`bash`/`grep` 且目标落在受保护集合 → 返回 `{block:true, reason}` |
| 失效①（成功） | 工具成功返回（清洗 md 已产出、临时目录已删）→ **立即注销**该任务 |
| 失效②（失败/中止） | 保持登记直到 **`agent_end`（本轮结束）** 才注销，防止失败残留 raw md 被读 |
| 失效③（兜底） | `agent_end` 无条件清空**所有**任务登记 |

**受保护对象**：本次输入 PDF + 本次临时工作目录（raw zip/md/图）。
**不保护（保持可读）**：清洗后的 `<pdf名>.md`、`images/`、其他任何文件。

> 即：限制的生命周期 = 「`pdf2md` 被调用」到「清洗 md 产出（成功）或本轮结束（失败兜底）」，
> **绝不整会话常开**；任务结束后，AI 读 PDF 的能力自动恢复。

### 7.3 已知缺口（已接受）

- **工具调用之前的窗口不拦**：但 pi 的 `read` 不支持 PDF，且 raw md 此时尚未生成，浪费可忽略；
  故不做意图预激活（避免 AI 理解与 token 消耗）。
- **`bash` 只能按命令串做路径匹配**，属尽力而为；真正的硬保险是「临时产物即删」。

---

## 8. 终端入口 `~/.local/bin/pdf2md`

```bash
#!/usr/bin/env bash
exec <mineru-env>/bin/python \
     ~/.agents/skills/pdf2md/scripts/pdf2md.py "$@"
```

用途：完全不经 AI、真·零 token；供手动或脚本/定时任务使用。

---

## 9. 批量与模型复用

- **默认**：逐篇调用 `mineru-kit parse`（flash 每次约 6 秒，不加载大模型，无需服务）。
- **批量且需要 standard**：起一次常驻服务复用 VLM：

```bash
<mineru-env>/bin/mineru-kit api-server \
    --host 127.0.0.1 --port 18765 --tier standard --preload-models
# 之后逐篇：
<mineru-env>/bin/mineru-kit parse "<pdf>" \
    -o "<tmp>/out.zip" -f zip --remote-url http://127.0.0.1:18765
```

生命周期与防残留：

- 启动前用 `ss -ltn` 探测 18765 是否空闲；若已有**自己的健康服务**则复用，否则报错；
- 健康检查通过后再开始；
- 用 `try/finally` + `trap` **必杀**子进程（按进程组），并写 `.pid` 文件；
- 崩溃残留：启动时若发现陈旧 pid，先清理。

---

## 10. Token 策略总结

| 环节 | 花费 |
|---|---|
| AI 触发转换（调 `pdf2md` 工具） | 极低：一个工具调用 + 一行回执 |
| AI 读 PDF | **0**（拦截 + read 不支持 + 无必要） |
| AI 读 raw md | **0**（临时即删 + 拦截） |
| AI 读清洗后 md（未来分析） | 允许但非必要不用读，约 60 KB/篇，无 base64、无参考文献 |

---

## 11. 验收用例

1. **单篇 · 纯文本 PDF** → 生成 `<pdf名>_md/<pdf名>.md + images/`；md 无 `data:image`；参考文献已裁；打印判档理由。
2. **单篇 · 扫描件** → 自动 `flash+ocr`，有正文文本。
3. **单篇 · 公式论文** → 自动 `standard`，或 `low_confidence` 时 AI 询问。
4. **AI 试图读 PDF / raw md** → 被扩展 block，并给出 reason。
5. **转换结束后** → 同一会话内 AI 可正常读其他 PDF（限制已解除）。
6. **批量** → 每篇在各自目录生成 `<pdf名>_md/`；用户未指定时 AI 先提醒。
7. **终端** `pdf2md a.pdf b.pdf` → 不经过 AI，产物正确。
8. **失败用例**（PDF 不存在）→ 退出码 1，错误信息含阶段与临时目录。

---

## 12. 实现顺序建议

1. `pdf2md.py`：mineru 调用 + zip 解包 + 清洗规则 + 输出结构 + 临时目录生命周期；
2. `~/.local/bin/pdf2md` 终端入口，先用终端跑通验收 1/2/7；
3. `SKILL.md`（pdf-extract）；
4. `mineru-pdf2md.ts`：先做 `pdf2md` 工具，再做拦截；
5. 档位启发式 + `low_confidence`，补验收 3；
6. 批量 standard 的服务复用，补验收 6。

---

## 13. 决策索引（Q → 定案）

| 问题 | 定案 |
|---|---|
| 交付形态 | 脚本 + pdf-extract skill + `pdf2md` 工具 + 终端命令 |
| 取图方式 | `-f zip`（外部图片，无 base64） |
| 图片引用 | 保留 `![](images/xxx.jpg)` |
| raw md | 不持久化，临时目录，成功即删 |
| 作者/单位 | v1 不裁；裁参考文献/抬头/页脚 |
| 清洗底线 | fail-safe，不伤正文 |
| 档位路由 | 扫描件→ocr；公式→standard；文本→flash+txt |
| 兜底 | 打印理由 + `--tier` 重跑 + 低置信度询问 |
| 输出位置 | 各 PDF 旁建 `<pdf名>_md/`；`--out` 可覆盖；批量未指定先提醒 |
| 模型复用 | flash 逐篇；standard 批量起 api-server，跑完必杀 |
| 拦截 | 做；默认 OFF；工具调用激活；成功即注销、失败到 `agent_end`、`agent_end` 兜底 |
| 终端命令 | 做 |
| 脚本语言 | Python（复用 mineru 环境） |
| skill 拆分 | `pdf2md` 只转换；分析另做 |

---

## 14. 待用户签字

- [x] 以上全部共识确认无误；
- [x] 是否同意按 §13 的顺序实现。

---

## 15. 变更留痕（实现前）

### 15.1 命名统一：`pdf2md` → `pdf-extract`

原文档中的 `pdf2md` 一律更名为 **`pdf-extract`**：

- skill 名与全局 skill 目录 → `pdf-extract`；
- 终端命令 → `pdf-extract`；
- pi 工具名 → `pdf-extract`；
- 扩展文件名 → `mineru-pdf-extract.ts`；
- 本实现仓库 → `~/projects/pdf-extract`，feature slug → `pdf-extract`。

§4/§5/§6/§7/§8/§12 与决策表中的旧名一律以本节为准。

### 15.2 仓库与安装的隔离

- 仓库（`~/projects/pdf-extract`）是**唯一源码与版本管理处**；
- 全局三处（skill / 扩展 / `~/.local/bin`）是**拷贝出来的安装产物**，由安装脚本从仓库拷贝；
- 二者边界清晰、互不干扰；代价是改完仓库需重跑安装脚本才生效；
- **不采用软链**。

### 15.3 测试接缝（T1）

- (a) 纯函数接缝：`clean(markdown) → (清洗后文本, 统计)`，不依赖 MinerU；
- (b) 薄集成接缝：`convert(pdf, 输出目录) → 产物`，用真实文本 PDF 验证端到端。
