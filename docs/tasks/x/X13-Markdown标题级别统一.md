# X13 Markdown 标题级别统一

状态：待开始
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

- [ ] 测试：同一份多级标题内容，分别作为 `.md` 文件（docling、builtin）、docx（docling、markitdown）解析，标题数和每个标题的级别完全一致，`#`/Word 标题 1 都是 1 级。
- [ ] 测试：pptx（markitdown）的幻灯片标题级别符合本卡规则，页码不变。
- [ ] 测试：HTML 的 `<h1>`～`<h3>` 级别与本卡规则一致。
- [ ] `make parser-lint parser-test` 通过。

## 问题

## 完成记录
