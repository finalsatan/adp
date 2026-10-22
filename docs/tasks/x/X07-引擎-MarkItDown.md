# X07 引擎：MarkItDown

状态：待开始
前置：X02、X03
被依赖：X10

## 目标

接入 MarkItDown 作为 Word、PPT 的快速解析引擎。

## 依据

- 知识库技术方案 6.2 的 MarkItDown 一行、4.1
- 需求 4.1.4（Word、PPT 可选 Docling、MarkItDown、MinerU）

## 要做

- 支持 docx、pptx（旧格式同 X06，由 worker 先转换，收到 doc、ppt 等返回 `UNSUPPORTED_TYPE`）。
- 先用 MarkItDown 转成 Markdown，再用 Docling 的 Markdown 后端转成 DoclingDocument（知识库技术方案 6.2）。
- PPT：按 MarkItDown 输出的页标记（锁定版本的实际格式，如 `<!-- Slide number: N -->`）给每页的元素补上页码。
- MarkItDown 只装 Word、PPT 需要的可选依赖，不装音视频、Azure 等无关部分（补充）。

## 文件

- 新建：`parser/app/engines/markitdown_engine.py`、`parser/tests/engines/test_markitdown.py`
- 修改：`parser/app/engines/registry.py`、`parser/pyproject.toml`

## 验收标准

- [ ] 快照测试：与 X06 共用 docx、pptx 样例，标题数、表格数正确；pptx 的每个元素都有正确的页码；doc 返回 `UNSUPPORTED_TYPE`。
- [ ] `make parser-lint parser-test` 通过。

## 问题

## 完成记录
