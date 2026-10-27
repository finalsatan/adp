# X07 引擎：MarkItDown

状态：待验收
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

- [x] 快照测试：与 X06 共用 docx、pptx 样例，标题数、表格数正确；pptx 的每个元素都有正确的页码；doc 返回 `UNSUPPORTED_TYPE`。
- [x] `make parser-lint parser-test` 通过。

## 问题

按卡片字面实现后，有以下几处行为差异。本卡验收标准（标题数、表格数、页码）都通过，
没有卡住开发；是否要改请确认。

1. **Word 的标题级别被 Docling 的 Markdown 后端平移了一级。** MarkItDown 把 Word 的
   一级标题转成 `#`、二级转成 `##`；Docling 的 Markdown 后端把 `#` 当文档标题
   （`label=title`），其余级别减一（`##` → 1 级、`###` → 2 级）。知识库技术方案 6.2 的
   Docling 行写的是“Word 保留标题层级”，本卡对 MarkItDown 只写了“先转 Markdown，再用
   Docling 的 Markdown 解析”，按字面做就是这个结果（`parser/tests/engines/test_markitdown.py`
   的 `test_docx_headings_and_table_snapshot` 里记录了实际映射）。
   建议：如果需要 Word 一级标题 = 1 级（“按标题”分块和引用里的标题路径更直观），要么在
   本引擎里把 MarkItDown 输出的标题级别平移，要么在 Docling 的 Markdown 后端（X06 的
   `docling_engine.py`）统一处理，让 `.md` 文件和 Word、PPT 走同一套映射。

2. **MarkItDown 不导出图片内容。** docx 里的图片 mammoth 不带出来，pptx 里只有
   `![alt](PictureN.jpg)` 这样的引用（文件不存在），Docling 的 Markdown 后端在
   `fetch_images=False` 下只保留图片元素和说明文字，没有图像数据。知识库技术方案 6.2 说
   “引擎抽取的图片存为 PNG”，MarkItDown 这条路径做不到；后处理和引用预览拿不到原图
   （`postprocess.save_images` 会跳过没有图像的图片元素，不会报错）。
   建议：在方案里注明 MarkItDown 引擎不抽图，或需要图片时选 Docling、MinerU。

3. **docx 表格多一行空表头。** mammoth 把 Word 表格转成不带 `<th>` 的 HTML，MarkItDown
   因此补一行空表头，原文的表头变成表格第二行（表格数、行列数正确，表头内容错位一行）。
   卡片没写这种情况。建议：如果要求表头正确，在引擎里按首行补表头，或换用 Docling 的
   Word 原生解析。

4. **分页依赖 MarkItDown 的页标记。** 幻灯片正文里如果出现同样的一行
   `<!-- Slide number: N -->`，会被当成新的分页；当前不做额外校验（MarkItDown 自己也是
   按幻灯片顺序输出这些标记）。影响很小，先记下。

## 完成记录

### 新建

- `parser/app/engines/markitdown_engine.py`：MarkItDown 引擎。docx、pptx 先用 MarkItDown
  转 Markdown，再用 Docling 的 Markdown 后端（`docling.backend.md_backend`）转成
  DoclingDocument；PPT 按 `<!-- Slide number: N -->` 分页，逐页解析后合并，给每个元素
  补上所在页码（Markdown 没有坐标，包围框留空），页面尺寸用 python-pptx 从文件里读
  （EMU 换算成点）；Word 保留 MarkItDown 的标题层级、不设 `prov`（需求 4.1.8）。
- `parser/tests/engines/test_markitdown.py`：15 个用例（X02 样例、多级标题 docx 快照、
  三页 pptx 快照与页码、表格、损坏文件、页标记切分、旧格式和类型不支持、注册表）。
- `parser/tests/engines/office_samples.py`：现场生成样例（多级标题 + 表格的 docx 约 37KB、
  三页带表格/图片/备注的 pptx 约 36KB），不往仓库提交二进制。

### 修改

- `parser/app/engines/registry.py`：`bootstrap()` 注册 `markitdown` 引擎。
- `parser/pyproject.toml`：加 `markitdown[docx,pptx]==0.1.9`（只装 Word、PPT 的转换器
  依赖）、`docling==2.142.1`（Markdown 后端；与已锁定的 docling-core 2.103.1 兼容，uv
  解析通过）；dev 组加 `python-docx`、`python-pptx`（生成样例用）。
- `parser/uv.lock`：随依赖更新。
- 本卡片：状态、问题、完成记录。

### 验收命令

```
$ make parser-lint
cd parser && uv run ruff check . && uv run ruff format --check .
All checks passed!
39 files already formatted

$ make parser-test
tests/engines/test_builtin.py ............                               [ 10%]
tests/engines/test_markitdown.py ...............                         [ 22%]
tests/engines/test_pypdfium.py ...........                               [ 32%]
tests/test_api.py .............                                          [ 43%]
tests/test_convert.py ......                                             [ 48%]
tests/test_filecheck.py ..............................................   [ 87%]
tests/test_pool.py ...                                                   [ 89%]
tests/test_postprocess.py ............                                   [100%]
================ 118 passed, 13 deselected, 1 warning in 27.95s ================
```

快照测试覆盖到的行为：

- X02 的 `tests/samples/sample.docx`：1 段正文、没有标题和表格、没有页码。
- 多级标题 docx：标题 3 个（`title` + 两个 `section_header`）、段落 3 个、表格 1 个
  （4 行 2 列）、所有元素不设 `prov`、`pages=0`。
- 三页 pptx：`pages=3`、`document.pages` 三页、按顺序的每个元素（标题、正文、表格、
  图片、图片说明、备注）页码都与所在幻灯片一致、`validate_document()` 通过。
- `doc`、`rtf`、`odt`、`ppt`、`odp` 返回 `UNSUPPORTED_TYPE`，说明“请先将文件转换为
  docx/pptx 后再解析”。

另外做了一次不提交的端到端验证（TestClient + moto 提交作业，走 worker 子进程、
后处理、对象存储）：`engine=markitdown` 的 docx、pptx 作业都 succeeded，pptx 的
`pages=3`、`engine_version=markitdown-0.1`。

### 和卡片不一致的地方

1. **样例。** 卡片说与 X06 共用 docx、pptx 样例；X06 的样例还没提交，本卡先用 X02 的
   `tests/samples/sample.docx`，其余样例由 `parser/tests/engines/office_samples.py`
   现场生成（X06 的样例提交后合并统一，见卡片“文件”一节之外的这处新增文件）。
2. **`supports()` 在类型层面支持 word、ppt。** `doc`、`rtf`、`odt`、`ppt`、`odp` 也返回
   True，解析时才返回 `UNSUPPORTED_TYPE`。原因：`/v1/engines` 用每种类型的第一个扩展名
   探测支持的类型（`parser/app/main.py`），只认 docx/pptx 会让 MarkItDown 不出现在
   Word、PPT 的引擎列表里；内置引擎处理 excel（xls、ods 先转换）也是这个做法。
3. **只注册 docx、pptx 两个转换器。** MarkItDown 自带的纯文本、压缩包等兜底转换器会把
   损坏的 docx、pptx 当纯文本转出乱码，这里不注册它们，损坏文件返回 `FILE_CORRUPTED`；
   缺可选依赖时返回 `ENGINE_UNAVAILABLE`。属于卡片“只装 Word、PPT 需要的可选依赖”的
   延伸。
4. **依赖里加了 docling。** 本引擎要用 Docling 的 Markdown 后端，但装 docling 是 X06 的
   职责（本卡“前置”里没有 X06）。为了本卡在 X06 之前能跑通，`pyproject.toml` 里加了
   `docling==2.142.1`；X06 合并时以它锁定的版本为准，两条合成一条。

### 留下的问题

- 见“问题”一节（标题级别、图片、表格表头、页标记）。
- X06 合并后建议把 `markdown_to_document()` 抽成共用函数：X06 的 HTML、Markdown 后端也
  要用 Docling 的 Markdown 后端，本卡先放在 `markitdown_engine.py` 里。
- MarkItDown 首次导入（magika、onnxruntime）在冷启动的机器上要十几秒，子进程池启动时
  会各付一次；X01 的池是启动完成后才报 ready，所以只影响服务启动时间，不影响作业超时。
