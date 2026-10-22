# 所有解析引擎的输出统一转换成 DoclingDocument

平台同时使用多个解析引擎：Docling、MinerU、pypdfium、MarkItDown、内置的表格和文本解析，以及可选的 PaddleOCR。下游的分块、引用定位和解析结果编辑不应该关心文件是哪个引擎解析的。2026-10-22 决定：每个引擎的输出都先转换成 docling-core 的 DoclingDocument，作为统一的解析结果来存储。选它的原因：它带标题层级、表格结构、阅读顺序，以及每个元素的页码和坐标；docling-core 现成提供基于它的分块器（按层级、按长度、按页）和 Markdown 导出；换引擎不影响下游。

**Considered Options**：
- 各引擎各自输出 Markdown，另建一张"Markdown 区间 → 原文位置"表（WeKnora DocReader 的做法）。这样位置映射要自己维护。
- 用 MinerU 的 `middle_json` 作为统一格式。它没有现成的分块器。

**Consequences**：
- Docling 以外的引擎（MinerU、PaddleOCR、pypdfium、MarkItDown、内置解析）都要写一个转换适配器。
- 下游依赖 docling-core 的数据结构，必须锁定版本；每次升级都要跑解析和分块的快照测试。
