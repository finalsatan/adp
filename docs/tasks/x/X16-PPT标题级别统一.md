# X16 PPT 标题级别统一

状态：已验收
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

无。卡片“要做”第 2 条的实测结论见“完成记录”。

## 完成记录

完成（2026-10-27）。

### 修改

- `parser/app/engines/docling_engine.py`：`_parse()` 里解析 pptx 后调用 X13 的
  `normalize_heading_levels()`，把 Docling 输出的幻灯片标题 `title` 换成 1 级
  `section_header`（`elif ctx.file_type == "ppt"` 分支，HTML 分支不变）。替换由共用
  函数完成，`self_ref`、父子/`children` 引用、`prov`（页码和包围框）都保留。
- `parser/tests/engines/test_headings.py`：新增
  `test_pptx_slide_titles_match_between_engines`。
- 本卡片：状态、问题、完成记录。

### 卡片第 2 条的实测结果（Docling 原生 pptx 除 `title` 外的标题标签）

用 `docling_samples.write_table_pptx()` 和临时构造的两份 pptx 直接跑
`DocumentConverter().convert()`，列出全部 `document.texts` 的 label、level、
content_layer、prov：

| 样例 | 输出 |
| --- | --- |
| 两页 pptx（标题占位符 + 表格） | 只有 `title`（2 个，BODY 层，带页码和 bbox），没有 `section_header` |
| Title Slide 版式（标题 + 副标题） | `title` + `paragraph`（副标题），没有 `section_header` |
| 正文里设大纲级别 0/1/2（模拟标题样式） | `paragraph` / `list_item`，没有 `section_header` |

结论：Docling 的 PPTX 后端只把幻灯片标题输出成 `title`，不会输出
`section_header`，所以 X13 的“其余级别加一”在 pptx 上没有可加的对象，两个引擎对
同一份 PPT 的级别天然一致，没有需要停下来确认的不一致。

### 验收标准逐条

- 同一份多页 pptx 两个引擎对比：`test_headings.py::test_pptx_slide_titles_match_between_engines`
  用 `write_table_pptx()`（2 页，标题 `First`/`Second`，第二页带表格）分别走 Docling
  和 MarkItDown，断言 `[(1, "First", 1), (1, "Second", 2)]`（级别、文字、`prov` 页码），
  并断言文档里没有 `title` 标签、`validate_document()` 通过。
- Docling 的页码、表格数断言不变：`test_docling.py::test_pptx_has_page_numbers_and_table`
  未改动，X06 的其余用例也全部通过（本卡改动前 192 个用例，加本卡的 1 个到 193）。
- 验收命令：

```
$ make parser-lint
cd parser && uv run ruff check . && uv run ruff format --check .
All checks passed!
61 files already formatted

$ make parser-test   # DOCLING_ARTIFACTS_PATH=$HOME/.cache/adp-docling-models HF_HUB_OFFLINE=1
193 passed, 17 deselected, 21 warnings in 83.27s (0:01:23)
```

### 和卡片不一致的地方

- 卡片“文件”一节写“修改 `parser/tests/engines/test_headings.py`（或 `test_docling.py`）”，
  选了 `test_headings.py`：跨引擎对比和 X13 的标题级别用例放在一起；`test_docling.py`
  未改（它原有的页码、表格数断言就是验收第 2 条要保护的对象）。
- 卡片说“改为 1 级 `section_header`，复用 `normalize_heading_levels()` 或同样的替换方式”，
  直接复用了共用函数，没有再写一份 pptx 专用的替换。

### 留下的问题

- 无。旧格式 ppt、odp 经 X02 转成 pptx 后走同一条路径（`ctx.file_type == "ppt"`），
  不需要单独处理，和卡片第 3 条一致。
