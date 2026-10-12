# 01: 单篇文本 PDF → 清洗后 Markdown

**What to build:** 给一个带文本层的 PDF，执行一次转换，即在其旁生成 `<文件名>_md/<文件名>.md` 与 `images/` 目录。Markdown 里的图片是外部文件并保留相对链接（`![](images/xxx.jpg)`，不含 base64）；参考文献、期刊抬头、版权/DOI 行、独立成行的页眉页脚页码被裁掉，正文不受损——正则定位不到锚点时不裁（fail-safe）。成功即删除临时目录；失败保留临时目录并报告其路径。运行打印所选档位与裁剪统计（行数/字符数）。错误以退出码区分：参数/路径错误、转换失败、清洗/写盘失败。清洗逻辑必须能脱离 MinerU 单独快速验证。

**测试接缝：**
- 纯函数接缝 `clean(markdown) → (清洗后文本, 统计)`：喂 markdown 字符串断言裁剪行为，不依赖 MinerU。
- 薄集成接缝 `convert(pdf, 输出目录) → 产物`：用真实文本 PDF 验证端到端。

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [x] 文本层 PDF 转换后生成 `<文件名>_md/<文件名>.md` 与 `images/`，md 中不含 `data:image`
- [x] 命中 `References` / `Bibliography` 标题后直到文末的内容被裁掉
- [x] 期刊抬头、版权/DOI 行、独立成行的页眉页脚页码被裁掉
- [x] 锚点定位不到时 fail-safe：不裁，正文原样保留
- [x] 打印所选档位与裁剪统计（行数/字符数）
- [x] 成功删除临时目录；失败保留并报告其路径
- [x] 退出码区分参数/路径错误、转换失败、清洗/写盘失败

## Comments

- 2026-10-01: 实现完成。核心在 `scripts/pdf_extract.py`，测试在 `tests/`（stdlib `unittest`，无新依赖）。
- 两个既定接缝均已落地：纯函数 `clean(markdown) -> (text, stats)`；薄集成 `convert(pdf, out_dir, *, runner=...) -> artifacts`（MinerU 子进程边界可注入）。
- 30 个测试通过；`PDF_EXTRACT_E2E=1` 时对真实文本 PDF 跑通 MinerU 端到端（默认跳过以保持快）。
- 真实论文冒烟：裁 107 行 / 12600 字符，正文与含 DOI 的致谢行保留，输出无 base64，`images/` 为外部文件。
- 附带实现了 `--force` 与退出码 4（超时）以便安全重跑；`--out`/批量/档位 auto 路由属工单 02/03，未越界实现。
