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

1. **单行图片的 OCR 不稳定（不影响本卡验收，记下备查）。** 实测同一张 900×220 的白底黑字图片，只改文字位置：y=160 识别正确；y=120/80/40 时 RapidOCR 的检测结果为空（Docling 日志 `The text detection result is empty`）；文字被画布裁掉一半（y=180）反而能识别但认错（`WEBP` → `WERR`）。同一张图换成三行文本，任何位置都能完整识别。所以本卡样例改用多行「文档样」图片（`docling_samples.py` 的 `WEBP_LINES`、`SCAN_PDF_LINES`）。这属于 Docling / RapidOCR 的行为，不是本卡的实现问题；X10 拿真实扫描件实测时要注意「稀疏的单行图片可能识别不出」。
2. **镜像大小（待用户拍板）。** Docling 依赖的 torch 在 linux/amd64 上默认带 CUDA 轮子（nvidia-cudnn 527MB、cublas 403MB、torch 529MB、triton 236MB 等，共约 3GB），parser 镜像约 7.5GB；而知识库技术方案里 Docling 是纯 CPU 跑、不需要 GPU。建议改用 CPU 版 torch（依赖从 3GB 降到约 400MB、镜像约 4GB），需要在 `pyproject.toml` 里指定 pytorch 的 CPU 索引；官方 `download.pytorch.org` 在当前构建环境不通，只能指向第三方镜像地址，所以没自行改。
3. **X01 的 pool 没有加载模型的钩子（已补）。** 知识库技术方案 6.3 要求「每个进程启动时加载一次 Docling 模型」，但 X01 的 `_worker_main` 只调 `bootstrap()`。本卡给 `Engine` 协议加了可选的 `warmup()`，pool 在报到 ready 前调用，改动记在完成记录里。

## 完成记录

阶段性提交（2026-10-27）：代码与测试完成并在本机验证通过；镜像内的快照测试、断网运行和资源实测还没跑（见「遗留」）。

### 新建

- `parser/tests/engines/docling_samples.py`：X06 的样例现场生成（文字层 PDF、扫描 PDF、webp、mhtml、带远程图片的 HTML，以及与 X07 共用的多级标题 docx、带表格的 pptx），不往仓库提交二进制。

### 修改

- `parser/app/engines/docling_engine.py`：
  - OCR 固定用 `RapidOcrOptions(backend="onnxruntime", lang=["ch"])`，不再用 Docling 的 `OcrAutoOptions`（自动挑选在没装 Tesseract/EasyOCR 的镜像里会挑到别的引擎或不可用；镜像里预置的正是 `onnxruntime:iso:zh` 那一份）。
  - 接上 Docling 2.142.1 的 `progress_callback`（`PageCompletedProgress`）做页级进度上报；原来只有开始、结束两次。
  - 转换器按 `scan_mode` 缓存（原来切 auto/force 会重建转换器、重新加载模型）。
  - 新增 `warmup()`：`initialize_pipeline(InputFormat.PDF)`，把版面、表格、RapidOCR 模型读进内存。
- `parser/app/pool.py`、`parser/app/engines/base.py`：`Engine` 协议加可选的 `warmup()`（注释形式，不强制各引擎实现）；子进程 `bootstrap()` 之后、报到 ready 之前逐个调用，失败只记日志、仍报到（否则子进程永远不就绪）。
- `parser/pyproject.toml`、`parser/uv.lock`：加 `onnxruntime>=1.20`（RapidOCR 的 onnx 推理后端，缺它 OCR 报 `onnxruntime is not installed`）；lock 里随之加入 flatbuffers、protobuf，另有 uv 0.13 的 marker 规范化 churn（`revision` 3→5，不改版本）。
- `parser/Dockerfile`：模型下载命令核对有效（`docling-tools models download layout tableformer rapidocr --rapidocr-backend-lang onnxruntime:iso:zh`，`iso:zh` 是 BCP-47 标签、映射到 PP-OCR 的 `ch`）；新增三个**可选**构建参数 `DEBIAN_MIRROR`、`PYPI_MIRROR`、`HF_ENDPOINT`，留空时行为与原来完全一致（`--frozen` 下 uv 只用 uv.lock 里记录的地址，所以 PYPI_MIRROR 是在镜像内改写 lock 的地址）；`HF_HUB_DISABLE_XET=1` 让 hf-mirror 这类不代理 Xet 的镜像可用。
- `Makefile`：`parser-test-docker` 支持 `PARSER_BUILD_ARGS` 透传。
- `parser/tests/engines/test_docling.py`：改用 `docling_samples.py`，并新增 3 个用例（页级进度、warmup 与转换器复用、多页文字层 PDF 的 force 页数）。
- 本卡片的「问题」「完成记录」。

### 验收命令结果

- `make parser-lint`：`All checks passed!` / `40 files already formatted`。
- `make parser-test`（`DOCLING_ARTIFACTS_PATH=/tmp/docling-models HF_HUB_OFFLINE=1`）：`111 passed, 13 deselected, 21 warnings in 101.86s`。用构建镜像时下载的那套模型目录 + 强制离线跑通，等于验证了「模型在本地目录时不再联网」。
- 本机实测（WSL2 / x86_64 / 16 核，预热后）：模型加载 3.39s；单页普通 PDF 0.67s（首次）、0.05s（后续）；单页扫描 PDF 1.12s（首次）、0.35s（后续）；RSS 峰值 普通 PDF 约 2.0GB、扫描 PDF 约 2.5GB。镜像内的数字待补。

### 遗留（下次继续）

- `make parser-test-docker` 还没跑通：镜像构建卡在 `uv sync` 下载 torch 的 3GB CUDA 依赖（清华源 20–200KB/s，已跑 2 小时仍在进行）。
- 镜像大小、容器内断网跑快照测试、单页普通/扫描 PDF 的耗时与内存峰值：待镜像构建完成后补进完成记录。
- 环境侧（不在仓库里）：`/etc/docker/daemon.json` 加了两条可用的 registry mirror（原文件备份 `/etc/docker/daemon.json.bak-x06`），否则 `FROM python:3.12.3-slim` 解析不了。
