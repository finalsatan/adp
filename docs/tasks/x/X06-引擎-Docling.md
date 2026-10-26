# X06 引擎：Docling

状态：进行中
前置：X02、X03
被依赖：X10

## 目标

接入 Docling：PDF 和图片走标准 PDF 管线（版面、表格结构、CPU OCR），Word、PPT、HTML、Markdown 走 Docling 的原生解析；同时补齐 `builtin` 引擎的 Markdown、HTML 部分。镜像内预置模型，离线可用。

## 依据

- 知识库技术方案 6.2 的两行 Docling、6.3（离线：构建时下载模型）、6.4（HTML 不抓远程资源）、4.1（Docling、RapidOCR、Pillow）
- 需求 4.1.4（Docling 的扫描页判断：逐页自动判断 / 全部按扫描件处理）、4.1.8（Word 没有页码）

## 要做

### 1. PDF、图片

- Docling 标准 PDF 管线：版面模型、TableFormer、RapidOCR（CPU）。
- 引擎参数 `scan_mode`：`auto`（默认，只对没有文字层的区域做 OCR）、`force`（对应 `force_full_page_ocr`）。
- 图片：png、jpg、jpeg、bmp、tiff 直接处理；webp、gif、jp2 先用 Pillow 转成 PNG（gif 取第一帧，补充）。
- `ocr_pages` 统计实际做了 OCR 的页数。
- 输出的标题、段落、表格、图片都带页码和坐标（Docling 原生提供）。
- 进度：按页报告（Docling 有页级回调就用，没有就在开始和结束各报一次，在完成记录里说明）。

### 2. Word、PPT、HTML、Markdown

- docx、pptx、html、md 用 Docling 的原生后端。Word 保留标题层级，没有页码（需求 4.1.8）；PPT 每页一个页码。
- doc、rtf、odt、ppt、odp 不在这里转换：worker 会先调用 `/v1/convert`（X02）转好再提交作业。收到这些扩展名时返回 `UNSUPPORTED_TYPE`，说明“请先转换为 docx / pptx”（补充：转换由流水线负责，知识库技术方案 8.1 第 1 步）。
- mhtml、mht：用标准库 `email` 取出 HTML 正文和内嵌图片，再交给 HTML 后端（知识库技术方案 6.2）。
- HTML：不执行脚本、不抓取远程图片和资源（知识库技术方案 6.4）。查清 Docling HTML 后端是否会请求远程图片，会的话关闭或拦截，用测试证明。
- `builtin` 引擎的 `markdown`、`html` 两类在内部调用这里的 Docling 后端（需求里叫“内置解析”，知识库技术方案 6.2 说明由 Docling 处理）。

### 3. 镜像和离线

- 安装 docling（锁版本，docling 和 docling-core 的版本要匹配）。
- 构建镜像时用 Docling 提供的模型下载命令把版面、表格、RapidOCR 模型下载到镜像内目录，运行时通过 `artifacts_path`（或 `DOCLING_ARTIFACTS_PATH`）指向它。具体命令以锁定版本的 `--help` 为准（知识库技术方案 6.3）。
- 每个子进程启动时加载一次模型（X01 的 pool 初始化钩子）。

## 文件

- 新建：`parser/app/engines/docling_engine.py`、`parser/app/engines/mhtml.py`、`parser/tests/engines/test_docling.py`、样例文件（扫描 PDF 一页、多级标题 docx、带表格的 pptx、带远程图片的 html、mhtml、webp）
- 修改：`parser/app/engines/registry.py`、`parser/app/engines/builtin.py`、`parser/Dockerfile`、`parser/pyproject.toml`

## 验收标准

- [ ] 快照测试（在镜像中运行，`make parser-test-docker`）：扫描 PDF 在 `auto` 下识别出文字且 `ocr_pages=1`；带文字层的 PDF 在 `auto` 下 `ocr_pages=0`、在 `force` 下等于页数；多级标题 docx 的标题数和级别正确、没有页码；pptx 每页一个页码、表格数正确；webp 图片能识别出文字；mhtml 能解析出正文；html 中的远程图片没有被请求（用一个本地 HTTP 服务记录请求，断言为零次）。
- [ ] 断网运行（`docker run --network none`）时 Docling 引擎可用、能完成上面的 PDF 用例（证明模型已在镜像内）。
- [ ] `make parser-lint parser-test` 通过。

## 注意

- Docling 迭代快，锁版本；升级时重跑本卡的快照测试（知识库技术方案 20）。
- 在完成记录里写下：镜像大小、单页普通 PDF 和单页扫描 PDF 在 CPU 上的耗时和内存峰值，供 X10 实测参考。

## 问题

## 完成记录
