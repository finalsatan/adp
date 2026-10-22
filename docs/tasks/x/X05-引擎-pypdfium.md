# X05 引擎：pypdfium

状态：待开始
前置：X03
被依赖：X10

## 目标

实现最快的 PDF 引擎：只读文字层，不做 OCR，输出带页码和坐标的段落。

## 依据

- 知识库技术方案 6.2 的 pypdfium 一行、4.1（pypdfium2；段落合并、页眉页脚判断参考 WeKnora DocReader 的 `pdf_parser.py`，MIT，可摘抄并保留版权声明）、20（文档中的隐藏文字：pypdfium 引擎参考 DocReader 过滤不可见文字）
- 需求 4.1.4（pypdfium：只读文字层，最快，不做 OCR）

## 要做

- 用 pypdfium2 逐页读取文字和字符坐标，按行、再按段落合并（参考 `repos/WeKnora/docreader/parser/pdf_parser.py`；本机没有 `repos/WeKnora` 时从 GitHub 的 Tencent/WeKnora 仓库取这个文件阅读）。摘抄的代码保留原版权声明，并在文件头注明来源。
- 每个段落一个文字元素，`prov` 带页码和包围框（PDF 坐标，原点左下，单位点）。
- 页眉页脚：统计每页首、尾若干行，在多数页重复出现的行（数字页码可以按“只差数字”归为同一行）标为页眉、页脚，由 X03 的预处理决定是否删除。
- 过滤不可见文字（渲染模式为不可见、或与背景同色等，按 DocReader 的做法），防止文档中藏的文字被用来做提示词注入。
- 没有文字层的页：解析结果为空，页码记入 `empty_pages`（知识库技术方案 6.2：在结果中标出页码），不报错。
- 页数超过上限时直接失败 `TOO_MANY_PAGES`（X01）。
- 加密、损坏的 PDF 返回 `FILE_ENCRYPTED`、`FILE_CORRUPTED`。
- 按页报告进度。

## 文件

- 新建：`parser/app/engines/pypdfium_engine.py`、`parser/tests/engines/test_pypdfium.py`、样例 PDF（含一页扫描页、带页眉页脚的多页文档、带隐藏文字的文档；可以在测试里用 reportlab 等现场生成，避免提交大文件）
- 修改：`parser/app/engines/registry.py`、`parser/pyproject.toml`

## 验收标准

- [ ] 快照测试：多页文档的段落数、页数正确；每个段落的页码正确，坐标落在页面范围内；重复的页眉页脚被标出，开启预处理后被删除；隐藏文字不出现在结果中；扫描页的页码出现在 `empty_pages`；加密 PDF 返回 `FILE_ENCRYPTED`。
- [ ] `make parser-lint parser-test` 通过。

## 问题

## 完成记录
