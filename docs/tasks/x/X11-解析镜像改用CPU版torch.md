# X11 解析镜像改用 CPU 版 torch

状态：已验收
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

完成（2026-10-27）。

### 改动

- `parser/pyproject.toml`：
  - 新增 `[[tool.uv.index]]`（`name = "pytorch-cpu"`、`url = "https://download.pytorch.org/whl/cpu"`、`explicit = true`）和 `[tool.uv.sources]`（`torch`、`torchvision` 在 `sys_platform == 'linux'` 时走该索引；macOS 仍用 PyPI 的轮子）。
  - 把 `torch`、`torchvision` 写成 `[project] dependencies` 的直接依赖（不加版本约束，版本仍由 docling-ibm-models 的约束决定）。**原因**：实测当前 uv 只对**直接依赖**应用 `[tool.uv.sources]`，只加索引和 sources 时 `uv lock` 报 `Existing uv.lock satisfies workspace requirements`、lock 不变（`uv lock --upgrade-package torch` 也不变）；把 torch 加进直接依赖后 lock 立即出现 `2.14.1+cpu`。torchvision 同理（它原本从 PyPI 解析到 linux 的 CUDA 轮子）。
- `parser/uv.lock`：重新生成（`uv lock`）。torch 2.14.1 / torchvision 0.29.1 的版本号不变，只多出 linux 的 `+cpu` 分支（`source = { registry = "https://download.pytorch.org/whl/cpu" }`）；包数 155 → 138（删掉 19 个、多出 2 个 linux 分支），删掉的 19 个全是 CUDA 相关：`cuda-bindings`、`cuda-pathfinder`、`cuda-toolkit`、`nvidia-cublas`、`nvidia-cuda-cupti`、`nvidia-cuda-nvrtc`、`nvidia-cuda-runtime`、`nvidia-cudnn-cu13`、`nvidia-cufft`、`nvidia-cufile`、`nvidia-curand`、`nvidia-cusolver`、`nvidia-cusparse`、`nvidia-cusparselt-cu13`、`nvidia-nccl-cu13`、`nvidia-nvjitlink`、`nvidia-nvshmem-cu13`、`nvidia-nvtx`、`triton`。lock 里 `nvidia-*`、`cuda-*`、`triton` 均为 0 条。
  - linux 分支同时有 aarch64 和 x86_64 的 `+cpu` 轮子（torch、torchvision 都是），amd64 也没有 CUDA 包。
  - 注意：索引地址是 `download.pytorch.org`，lock 里记录的轮子地址是 `download-r2.pytorch.org`（各 13 / 5 处），两个域名构建时都实测可访问。
- `parser/Dockerfile`：**未改**。按第 4 条核对（在 lock 副本上模拟 `PYPI_MIRROR` 的 sed）：`https://pypi.org/simple` 和 `https://files.pythonhosted.org` 全部被改写（806 处），CPU 索引的地址一处没动（`download.pytorch.org` 13 处、`download-r2.pytorch.org` 5 处保持原样）。

### 验收命令结果

- `make parser-test-docker`（断网容器）：构建成功；`198 passed, 5 deselected, 21 warnings in 160.89s`（总耗时 8m51s）。Docling 的全部快照用例通过。
- 断网单跑：`docker run --rm --init --network none adp-parser:test pytest -q tests/engines/test_docling.py::test_pdf_scan_mode_reports_ocr_pages` → `1 passed, 8 warnings in 7.37s`。
- `docker run --rm --network none adp-parser:test uv pip list | grep -iE 'nvidia|triton|cuda|torch'` → 只有 `torch 2.14.1+cpu`、`torchvision 0.29.1+cpu`（镜像里没有 pip，`pip list` 不可用，改用 `uv pip list`）。
- `docker run --rm --network none adp-parser:test python -c "import torch; print(torch.__version__, torch.cuda.is_available())"` → `2.14.1+cpu False`。
- `make parser-lint`：`All checks passed!` / `59 files already formatted`。
- `DOCLING_ARTIFACTS_PATH="$HOME/.cache/adp-docling-models" make parser-test`（macOS 本机）：`186 passed, 17 deselected, 21 warnings in 88.92s`；本机 venv 里仍是 PyPI 的 `torch 2.14.1`、`torchvision 0.29.1`。

### 镜像大小（linux/arm64，Docker Desktop）

| | 改动前（X06 记录） | 改动后 |
| --- | --- | --- |
| `docker image inspect` 的 Size | 11,981,388,272 字节（约 11.98 GB） | 2,533,337,625 字节（约 2.53 GB） |
| `docker images` 的 DISK USAGE / CONTENT SIZE | 33.4 GB / 12 GB | 9.28 GB / 2.53 GB |

用同一个标签 `adp-parser:test` 重建，旧镜像被新镜像替换（没有手工删除，但重建后 `docker images -a` 里已看不到旧镜像 ID）。

### 镜像内耗时和内存（量法与 X06 相同）

Apple 芯片、linux/arm64 容器、断网、每种样例一个独立进程、先 `warmup()` 再首次解析；RSS 是进程从启动到解析完成的峰值（含模型预热）。两轮结果：

| 样例 | warmup | parse | 峰值 RSS |
| --- | --- | --- | --- |
| 单页普通 PDF（第一轮 / 第二轮） | 1.747s / 1.542s | 0.645s / 0.697s | 1155.7 / 1155.6 MiB |
| 单页扫描 PDF（第一轮 / 第二轮） | 1.738s / 1.810s | 0.959s / 1.051s | 1500.2 / 1466.2 MiB |
| X06 记录（普通 / 扫描） | 1.780s / 1.792s | 0.686s / 1.011s | 1494.1 / 1773.3 MiB |

耗时在噪声范围内一致（扫描 PDF 仍识别出 1 页 OCR）；内存峰值普通 PDF 低约 340 MiB、扫描 PDF 低约 270～310 MiB。

### 建议后续处理

- 用 `PYPI_MIRROR` 在国内构建时，PyTorch 的 CPU 索引（`download.pytorch.org` / `download-r2.pytorch.org`）仍要能直连，`PYPI_MIRROR` 不覆盖它；如需国内源构建，得另外给 `[[tool.uv.index]]` 配镜像地址。
- 升级 docling 时按知识库技术方案 20 重跑本卡和 X06 的镜像内验收。
