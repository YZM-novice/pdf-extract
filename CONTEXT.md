# Context

本仓库的领域语言。这里是**术语表**，不是规格、不是实现笔记。

## 文档结构 / document structure

一篇由 MinerU 转出的 markdown 在被清洗时，其可被独立识别的骨架：
**front matter**（标题之前）、**title**（第一个一级标题）、**body**（标题与参考文献之间）、
**back matter**（参考文献标题到文末），以及 **protected 行**（代码围栏、`<details>` 内的行）。

清洗规则只**读**文档结构，不各自重新推断它。protected 行对结构解析和所有规则都不透明：
一律不清洗。
