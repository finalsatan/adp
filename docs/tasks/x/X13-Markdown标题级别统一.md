# X13 Markdown 标题级别统一

状态：已验收
前置：X06、X07
被依赖：—

## 目标

经过 Docling Markdown 解析的文档（`.md` 文件、内置解析的 markdown、MarkItDown 的 Word 和 PPT），`#` 是 1 级标题，`##` 是 2 级，依此类推；和 Docling 原生解析 Word 得到的标题级别一致。“按标题分块”和引用里的标题路径不会因为引擎不同差一级。

## 依据

- 知识库技术方案 6.2（Docling 行：“Markdown 的标题级别与 Word 一致（# 为 1 级，不单独作为文档标题）”，2026-10-27 补）
- X07 卡“问题”1 及其决定
- 需求 4.1.4（分块方式“自动”：有标题层级按标题）

## 要做

1. 把 X07 的 `markdown_to_document()` 抽成 Docling 引擎和 MarkItDown 引擎共用的函数（X07 完成记录里的建议），放在 `parser/app/engines/` 下合适的模块里。
2. 在这个共用函数里修正级别：Docling Markdown 后端给出的 `title`（来自 `#`）改为 1 级标题，`section_header` 级别加一（`##` → 2）。最终文档里只用 `section_header` 表示标题，不再出现 `title`。
3. `.md` 文件（docling 引擎）、内置解析的 markdown（builtin 引擎）、MarkItDown 的 docx 和 pptx 都走这个函数。
4. 核对 Docling 原生解析 docx（Word 一级标题样式）和 HTML（`<h1>`）的级别：docx 应为 1 级；HTML 的 `<h1>` 如果也被当成 `title`，同样按本卡规则改为 1 级，并在完成记录写明。
5. 更新受影响的快照测试的期望值（只改标题级别相关的断言），不放宽其他断言。

## 文件

- 修改：`parser/app/engines/docling_engine.py`、`parser/app/engines/markitdown_engine.py`、`parser/app/engines/builtin.py`（如需要）、相关测试
- 新建：共用函数所在模块（如需要）

## 不做

- 不改 MinerU 的标题归一（X08 已按“最高一级为 1 级”处理）。
- 不改 X10 的统计逻辑（`title` 键以后自然为 0）。

## 验收标准

- [x] 测试：同一份多级标题内容，分别作为 `.md` 文件（docling、builtin）、docx（docling、markitdown）解析，标题数和每个标题的级别完全一致，`#`/Word 标题 1 都是 1 级。
- [x] 测试：pptx（markitdown）的幻灯片标题级别符合本卡规则，页码不变。
- [x] 测试：HTML 的 `<h1>`～`<h3>` 级别与本卡规则一致。
- [x] `make parser-lint parser-test` 通过。

## 问题

1. **Docling 原生解析 PPT 的幻灯片标题仍是 `title`（本卡范围外，建议后续处理）。**
   本卡只改了 Markdown 解析路径（`.md`、内置 markdown、MarkItDown 的 Word 和 PPT）和
   Docling 原生 HTML。实测 Docling 原生解析 pptx 时，幻灯片标题占位符输出的是 `title`
   标签、没有级别（`test_docling.py` 的 pptx 用例只断言页码和表格数，没断标题标签）。
   所以同一份 PPT 选 Docling 还是 MarkItDown，标题标签不一样（`title` 对 1 级
   `section_header`），“按标题分块”和引用里的标题路径可能不同。卡片“要做”第 4 条只
   让核对 docx 和 HTML，没提原生 PPT，本卡不改。建议：需要时开一张卡，把 Docling 原生
   PPT 的幻灯片标题也归一成 1 级 `section_header`（或在下游分块时把 `title` 当 1 级）。

## 完成记录

完成（2026-10-27）。

### 新建

- `parser/app/engines/markdown_doc.py`：Markdown 转 DoclingDocument 的共用实现（从
  X07 的 `markitdown_engine.py` 抽出）。`normalize_heading_levels()` 把 Docling 的
  Markdown、HTML 后端给出的 `title`（`#`、`<h1>`）改成 1 级 `section_header`，其余
  `section_header` 级别加一；`markdown_to_document(text, name)`、
  `markdown_file_to_document(path, name)` 都走同一套转换和修正。
- `parser/tests/engines/test_headings.py`：跨引擎的标题级别测试。

### 修改

- `parser/app/engines/docling_engine.py`：`.md` 不再走 Docling 的转换器，改调共用函数；
  HTML（含 mhtml）解析完调用 `normalize_heading_levels`（`_HTML_EXTS`）。
- `parser/app/engines/markitdown_engine.py`：删掉本地的 `markdown_to_document`，改从
  `markdown_doc.py` 导入（转换后多一步级别修正）。
- `parser/tests/engines/test_markitdown.py`：docx 快照的标题级别从 `title` + `[1, 2]`
  改为 `[1, 2, 3]`；pptx 快照的幻灯片标题标签从 `title` 改为 1 级 `section_header`，
  并补一条级别断言；页码等其它断言不变。
- 本卡片：状态、问题、完成记录。

### 标题级别的实际映射

| 来源 | 修正前 | 修正后 |
| --- | --- | --- |
| Markdown `#`（docling、builtin）、MarkItDown 的 Word 标题 1 | `title` | `section_header` 1 |
| Markdown `##`、MarkItDown 的 Word 标题 2 | `section_header` 1 | `section_header` 2 |
| Markdown `###` | `section_header` 2 | `section_header` 3 |
| HTML `<h1>` | `title` | `section_header` 1 |
| HTML `<h2>`、`<h3>` | `section_header` 1、2 | `section_header` 2、3 |
| Docling 原生 docx（Word 标题 1/2/3） | `section_header` 1/2/3 | 不变（本卡的对齐基准） |
| Docling 原生 HTML 的 `<title>` 元数据 | `title`（furniture） | 不变，由 X03 后处理删除 |

`TitleItem` 没有 `level` 字段，改成 `SectionHeaderItem` 时直接替换 `document.texts`
里的对象，保留 `self_ref`、父子和 `children` 引用（标题里的加粗等行内元素是它的子
元素，用 `document.replace_item` 会被一起删掉，所以没用它）。HTML 的 `<title>` 是
furniture 内容层的文档元数据，不算标题，不在修正范围内。

### 验收命令

```
$ make parser-lint
cd parser && uv run ruff check . && uv run ruff format --check .
All checks passed!
61 files already formatted

$ make parser-test   # DOCLING_ARTIFACTS_PATH=$HOME/.cache/adp-docling-models HF_HUB_OFFLINE=1
189 passed, 17 deselected, 21 warnings in 107.70s (0:01:47)
```

验收标准逐条：

- 同一份多级标题内容：`test_headings.py::test_markdown_and_word_heading_levels_match`
  用 `.md`（docling、builtin）、docx（docling、markitdown）解析，四个结果的标题数、
  级别、文字都等于 `[(1, "第一章 总则"), (2, "1.1 适用范围"), (3, "1.1.1 例外")]`，
  并且都没有 `title` 标签。
- pptx（markitdown）：`test_markitdown.py::test_pptx_pages_snapshot` 断言三张幻灯片
  标题都是 1 级，页码断言（`reports`、`document.pages`、每个元素的 `prov`）不变。
- HTML：`test_headings.py::test_html_heading_levels_match_markdown` 断言
  `<h1>`～`<h3>` 是 1～3 级；`test_html_title_tag_is_not_a_heading` 断言 `<title>`
  元数据不参与修正。
- `make parser-lint parser-test` 通过（输出见上；X07 提交时 118 个用例，X08、X10
  合进来后 186，本卡加 3 个跨引擎用例到 189）。

### 和卡片不一致的地方

- 卡片说“把 `markdown_to_document()` 抽成共用函数”，实现里共用模块有两个入口：
  `markdown_to_document(markdown, name)` 给 MarkItDown（内容是 str），
  `markdown_file_to_document(path, name)` 给 `.md` 文件（内容按字节交给 Docling 的
  `decode_text` 识别编码，和原来原生解析 `.md` 的行为一致）。两者共用 `_convert()`
  和级别修正，属于内部实现细节。
- 新增了 `parser/tests/engines/test_headings.py`（卡片“文件”一节写的“相关测试”）。

### 留下的问题

- 见“问题”一节：Docling 原生解析 PPT 的幻灯片标题仍是 `title`。
- 导出 Markdown（X03 的 `out/doc.md`）里 1 级 `section_header` 仍是 `##`：docling-core
  的序列化器约定 `title` 占 `#`、`section_header` 从 `##` 起。这是原来 Docling 原生
  docx 就有的行为（Word 标题 1 → `section_header` 1 → `##`），本卡只是让 Markdown、
  MarkItDown 对齐到同一套级别，导出结果不变，未改。
