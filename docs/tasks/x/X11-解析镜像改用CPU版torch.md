# X11 解析镜像改用 CPU 版 torch

状态：待开始
前置：X06
被依赖：X12

## 目标

parser 镜像不再装 CUDA 依赖，体积从约 12GB 降下来，Docling 在 CPU 上的行为和测试结果不变。

## 依据

- 知识库技术方案 6.3（Docling、MinerU 都在 CPU 上运行）、20（升级 Docling 时重跑快照）
- X06 卡“问题”2 与“镜像内验收与实测”（实测 linux/arm64 也装了 nvidia 依赖，测试镜像 11.98GB）
- 决定（2026-10-27 用户确认）：改用 CPU 版 torch，下载源用 PyTorch 官方 CPU 索引 `https://download.pytorch.org/whl/cpu`（本机实测可访问）。

## 要做

1. 在 `parser/pyproject.toml` 里用 uv 的 `[[tool.uv.index]]`（`explicit = true`）加官方 CPU 索引，并在 `[tool.uv.sources]` 里让 `torch`、`torchvision` 在 Linux 上走这个索引；macOS 本地开发仍用 PyPI 的轮子（macOS 的 PyPI 轮子本来就不带 CUDA）。具体写法以当前 uv 版本的文档为准。
2. torch、torchvision 的版本保持和现在的 uv.lock 一致（torch 2.14.1）；CPU 索引里没有同版本时，选最接近且与 docling 2.142.1 兼容的版本，并在完成记录里写明。
3. `cd parser && uv lock` 重新生成 lock；确认 lock 里 Linux 平台不再有 `nvidia-*`、`triton` 等 CUDA 包。
4. Dockerfile 的 `PYPI_MIRROR` 构建参数目前会改写 lock 里的地址：确认它只改写 PyPI 的地址、不会把 CPU 索引的地址也改掉；需要时最小改动。
5. 重新构建镜像，跑 X06 的全部镜像内验收。

## 文件

- 修改：`parser/pyproject.toml`、`parser/uv.lock`、`parser/Dockerfile`（只在第 4 条需要时）

## 不做

- 不升级 docling、docling-core 或其他依赖（只换 torch 的来源）。
- 不动 mineru 镜像（它不装 torch）。

## 验收标准

- [ ] `make parser-test-docker` 通过（断网容器）；Docling 的快照测试全部通过。
- [ ] 断网容器里 `tests/engines/test_docling.py::test_pdf_scan_mode_reports_ocr_pages` 通过。
- [ ] `docker run --rm adp-parser:test pip list` 或 `uv pip list` 里没有 `nvidia-*`、`triton`；`python -c "import torch; print(torch.__version__, torch.cuda.is_available())"` 输出 CPU 版且为 False。
- [ ] 完成记录写明：改动前后测试镜像的 `docker image inspect` Size；单页普通 PDF 和单页扫描 PDF 的耗时、内存峰值和 X06 记录的数字对比（同为 Apple 芯片 arm64 环境，量法和 X06 一样）。
- [ ] `make parser-lint parser-test` 通过（macOS 本机）。

## 注意

- linux/amd64 的 lock 也要是 CPU 版（生产服务器可能是 x86）。本机构建不了 amd64 镜像时，至少用 `uv lock` 的结果确认 amd64 的分支没有 CUDA 包。

## 问题

## 完成记录
