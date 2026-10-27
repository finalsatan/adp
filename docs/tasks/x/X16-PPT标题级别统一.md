# X16 PPT 标题级别统一

状态：待开始
前置：X13
被依赖：—

## 目标

Docling 原生解析 pptx 时，幻灯片标题是 1 级标题（`section_header` level 1），和 MarkItDown 解析同一份 PPT 的结果一致；同一份 PPT 换引擎，“按标题分块”和引用里的标题路径不变。

## 依据

- X13 卡“问题”1
- 决定（2026-10-27 用户确认）：PPT 的幻灯片标题也统一成 1 级。
- 知识库技术方案 6.2（Docling 行：PPT 每页一个页码）

## 要做

1. Docling 引擎解析 pptx 后，把幻灯片标题（Docling 输出的 `title`）改为 1 级 `section_header`，复用 X13 的 `normalize_heading_levels()`（`parser/app/engines/markdown_doc.py`）或同样的替换方式，保留页码、坐标和子元素。
2. 先实测 Docling 原生 pptx 除 `title` 外还会不会输出 `section_header`（如副标题、正文里的标题样式）。如果有，确认按 X13 的“其余级别加一”处理后，级别和 MarkItDown 对同一份 PPT 的结果一致；不一致时在“问题”里写清实测结果和建议，停下来等回复。
3. 旧格式 ppt、odp 经 X02 转成 pptx 后走同一路径，不需要单独处理。

## 文件

- 修改：`parser/app/engines/docling_engine.py`、`parser/tests/engines/test_headings.py`（或 `test_docling.py`）

## 不做

- 不改 MinerU 的 PPT 标题（X08 已按“最高一级为 1 级”归一）。

## 验收标准

- [ ] 测试：同一份多页 pptx 分别用 docling、markitdown 解析，每页的幻灯片标题都是 1 级 `section_header`，标题文字和所在页码一致，文档里没有 `title`。
- [ ] 测试：Docling 解析 pptx 的页码、表格数断言不变（X06 原有用例全部通过）。
- [ ] `make parser-lint parser-test` 通过。

## 问题

## 完成记录
