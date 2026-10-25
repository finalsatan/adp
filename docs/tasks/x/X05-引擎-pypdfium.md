# X05 引擎：pypdfium

状态：已验收
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

- [x] 快照测试：多页文档的段落数、页数正确；每个段落的页码正确，坐标落在页面范围内；重复的页眉页脚被标出，开启预处理后被删除；隐藏文字不出现在结果中；扫描页的页码出现在 `empty_pages`；加密 PDF 返回 `FILE_ENCRYPTED`。
- [x] `make parser-lint parser-test` 通过。

## 问题

无阻塞问题。

## 完成记录

### 新建

- `parser/app/engines/pypdfium_engine.py`：`PypdfiumEngine` + `parse_pdf`。用 pypdfium2 逐页取字符（`get_charbox(loose=True)`），过滤渲染模式不可见（`FPDFTextObj_GetTextRenderMode == FPDF_TEXTRENDERMODE_INVISIBLE`）和页面外的字符，按分栏线拆栏、按 y 中心合并成行、按行距合并成段落；每个段落一个文字元素，`prov` 带页码和 PDF 坐标包围框。
- `parser/tests/engines/test_pypdfium.py`：11 个用例（多页快照、页码与坐标、页眉页脚标出与删除、进度、隐藏文字、扫描页、加密、损坏、能力与注册）。
- `parser/tests/engines/pdf_samples.py`：用 reportlab 现场生成样例 PDF（多页带页眉页脚、隐藏文字、含图片扫描页、加密、损坏），不提交二进制。

### 修改

- `parser/app/engines/registry.py`：`bootstrap()` 注册 `PypdfiumEngine`（假引擎仍只在 `PARSER_ENABLE_FAKE_ENGINE=1` 时注册）。
- `parser/pyproject.toml`、`parser/uv.lock`：加 `pypdfium2==5.14.0`；dev 依赖加 `reportlab==5.1.0`（只用于测试生成样例 PDF）。
- 本卡片的“状态”“问题”“完成记录”。

### 验收命令结果

- `make parser-lint parser-test`：`All checks passed!`、`30 files already formatted`、`51 passed, 1 deselected`（integration 默认不跑）。新增 `tests/engines/test_pypdfium.py` 11 个用例。

### 和卡片不一致 / 补充实现细节

- **依赖**：本卡只新增 `pypdfium2`（5.14.0），没有安装完整 Docling（不拉 torch）；样例 PDF 用 `reportlab`（dev 依赖，5.1.0）现场生成，仓库不提交二进制。
- **行分组用 `loose=True` 的字符框**：紧致框下英文句点等标点因框偏下会被拆成单独一行（甚至被误判成页脚），改用字体的 ascent/descent 框后同一行字符高度一致。
- **隐藏文字只过滤渲染模式 3**：DocReader 的 `_collect_invisible_boxes` 也只处理 `FPDF_TEXTRENDERMODE_INVISIBLE`，卡片里的“与背景同色等”按 DocReader 的做法未单独实现。
- **页眉页脚标记**：重复判断只取每页首、尾的短行（≤80 字符），把行内数字替换成 `#` 后归一化（页码只差数字归为同一行），在至少 2 页、且达到 60% 页面时判为重复；命中的元素打 `PAGE_HEADER`/`PAGE_FOOTER` 标签，交给 X03 的预处理删除。
- **段落合并**：同一页内相邻两行间距不超过行高、且上一行不以句末标点结尾时合并为一段；合并行时两侧都是中日韩文字/标点则不插空格，否则插一个空格。
- **`TOO_MANY_PAGES`**：引擎返回总页数，页数上限由 X01 的 `runner.finish()` 统一判断（`output.pages > parser_max_pages`）；`ParseContext` 没有上限字段，本卡未在引擎里重复判断。
- **加密与损坏**：pypdfium2 打开失败时按 `PdfiumError` 文案区分——含 `password` 返回 `FILE_ENCRYPTED`，其余返回 `FILE_CORRUPTED`。
- **`engine_version`**：`pypdfium-0.1`。
- **来源声明**：行分组、行内空格推断、隐藏文字过滤、重复页眉页脚判断参考 WeKnora DocReader 的 `docreader/parser/pdf_parser.py`（MIT），已按思路用 pypdfium2 重写并在文件头注明。

### 遗留

- 多栏排版只做了按竖直分栏线拆栏的简化处理，复杂版面（表格、图文混排）的阅读顺序未做；本卡定位是最快档，不追求版面还原。
