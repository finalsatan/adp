# X08 MinerU 服务镜像与适配器

状态：待验收
前置：X03
被依赖：X10

## 目标

做出默认部署的 mineru 服务镜像（CPU，快速、基础两档），并在解析服务里写 MinerU 适配器：调用 MinerU 的 V1 接口，把结果转成 DoclingDocument。标准、高级档走同一个适配器，只是地址和密钥换成系统设置里配置的自建 GPU 服务或官方云端 API。

## 依据

- 知识库技术方案 K9、6.2 的 MinerU 一行及其后的说明、6.3（mineru 镜像、并发、离线）、6.4、18 的 mineru 一行、19.3 的“MinerU 适配器”、20（MinerU 的风险）
- 需求 4.1.4（MinerU 的解析档位；Word、PPT、HTML、EPUB、OFD 由 MinerU 直接读取，不需要选档位）

## 重要说明

本卡依赖的 MinerU 4 接口细节（`mineru-kit api-server`、V1 接口、middle_json 的 `docvortex.middle 2.0` 格式）来自知识库技术方案，当时**只核对了官方文档，没有实际运行**（知识库技术方案 4.1）。开工前先读 MinerU 官方文档和仓库中的示例脚本 `scripts/http_api_example.sh`；**与本卡描述不一致时以官方文档为准**，并在“问题”里记下差异。如果 MinerU 4 的 CPU 服务根本跑不起来，停下来报告，不要改用别的方案。

## 要做

### 1. mineru 镜像 `deploy/mineru/Dockerfile`

- 基于 Python 3.12，安装 `mineru>=4.0,<5` 的基础包（ONNX + llama.cpp，不装 PyTorch），锁具体版本（知识库技术方案 6.3）。
- 构建时下载基础档模型（约 0.8GB）进镜像，运行时不联网。
- 启动命令：`mineru-kit api-server --tier basic --api-key $MINERU_API_KEY --concurrency $MINERU_CONCURRENCY`（参数名以锁定版本的 `--help` 为准）。
- 健康检查用 `/v1/health`（知识库技术方案 18）。
- 在 `parser/compose.dev.yml` 里加上 mineru 服务，供本地开发和测试。

### 2. 适配器 `parser/app/engines/mineru/`

- 配置：默认部署的服务用环境变量 `MINERU_URL`、`MINERU_API_KEY`；标准、高级档用作业请求里的 `remote`（`base_url`、`api_key`，由主服务从系统设置取出传入）。
- 引擎参数 `tier`：`flash`、`basic`、`standard`、`advanced`。只对 PDF 和图片有效；Word、PPT、HTML、EPUB、OFD 固定用 `flash` 原生解析（知识库技术方案 6.2），旧格式（doc、ppt、xls、rtf、odt、odp）MinerU 直接读取，不需要转换（需求 4.1.3）。`standard`、`advanced` 没有 `remote` 时返回 `ENGINE_UNAVAILABLE`。
- 调用流程（知识库技术方案 6.2）：上传文件 → `POST /v1/tasks` 异步提交 → 轮询状态（同时把进度报给 X01 的回调）→ 下载 ZIP 结果（middle_json、Markdown、images/）。
- 上传的两种情况（知识库技术方案 6.2）：官方云端返回预签名地址，上传时**不能带** MinerU 密钥；自建服务的上传地址与接口同源，要带密钥。
- MinerU 服务重启导致作业丢失（查询返回 404 或等价错误）：本次作业失败，错误码 `REMOTE_ERROR`，说明“解析服务重启，请重试”（知识库技术方案 6.2：由 worker 重新提交）。
- 等待 MinerU 时不占用 Docling 的子进程（知识库技术方案 6.3）：MinerU 引擎在 X01 的主进程里用异步方式执行，不进子进程池（补充，若 X01 的结构不支持，在 runner 里为“远程引擎”开一条单独的执行路径）。
- `availability()`：调用 `/v1/health`，失败时返回“连接失败”。
- 单文件上限 200 MiB，与平台默认上限一致（知识库技术方案 6.2）。

### 3. middle_json → DoclingDocument

- 页码 = `page_idx + 1`。
- 块类型：标题（带级别）、正文、列表、表格（HTML，解析成含合并单元格的表格结构）、图片、公式（以文字形式保存原始公式，补充）。
- PDF 的块带坐标，换算成 PDF 坐标系（原点左下，单位点）；需要页面尺寸时从 middle_json 中取。
- 标题级别归一：实测 docx 的一级标题输出为 2 级（知识库技术方案 6.2），按实测结果写一个映射，让最高一级标题为 1 级。
- 图片从 ZIP 的 `images/` 中取出，交给 X03 统一保存。

## 文件

- 新建：`deploy/mineru/Dockerfile`、`parser/app/engines/mineru/{__init__,client,convert,engine}.py`、`parser/tests/engines/mineru/`（含实际 MinerU 输出的 middle_json 样例，做快照测试）
- 修改：`parser/app/engines/registry.py`、`parser/compose.dev.yml`、`parser/pyproject.toml`

## 验收标准

- [ ] 快照测试（知识库技术方案 19.3）：用 MinerU 实际输出的 middle_json 样例（PDF、docx、pptx 各一份，由你用本地 mineru 服务跑出来后提交），转换结果的标题级别、合并单元格表格、图片、公式、PDF 坐标正确。
- [ ] 测试（模拟 HTTP）：云端模式上传时不带密钥、自建模式带密钥；任务查询返回 404 时作业失败为 `REMOTE_ERROR`；`standard` 没有 `remote` 时返回 `ENGINE_UNAVAILABLE`；docx 不论传什么 tier 都用 `flash`。
- [ ] 集成测试（连本地 mineru 容器，标记 integration）：扫描 PDF 用 `basic` 档能解析出文字；docx、epub 各一份能解析。
- [ ] 断网运行 mineru 容器能完成解析（模型已在镜像内）。
- [ ] `make parser-lint parser-test` 通过。

## 注意

- 在完成记录里写下：mineru 镜像大小、基础档单页 PDF 的耗时和内存，供 X10 实测参考。
- MinerU 的许可证附加条款（知识库技术方案 4.1、20）本阶段不处理，不要在代码或界面里加声明。

## 问题

开工前核对了 MinerU 官方文档和仓库（4.0.12，仓库 commit `b7fdd0c`，含 `scripts/http_api_example.sh`）。
下面是和卡片不一致、或卡片和文档都没写清楚的地方。都已按“以官方为准 / 选最简单的做法”处理，
不影响接口契约和用户可见行为，记在这里备查。

1. **V1 接口和卡片写的不一样（以官方为准）**。卡片写“上传文件 → `POST /v1/tasks` 异步提交 → 轮询 → 下载 ZIP”。
   4.0.12 的 V1 接口是上传会话：`POST /v1/uploads` → `PUT upload_url` → `POST /v1/uploads/{id}/complete`
   → `POST /v1/parse/jobs` → `GET /v1/parse/jobs/{id}` → `GET /v1/files/{file_id}/content`；另有一套便捷接口
   `POST /v1/tasks`（原始字节）+ `GET /v1/tasks/{id}/result?response_format=zip`。适配器按上传会话这套实现，
   因为卡片要求的“官方云端返回预签名地址、上传时不带 MinerU 密钥”只在这套接口里存在（`upload_url` 可能与接口不同源）。
   启动参数 `mineru-kit api-server --tier basic --concurrency --api-key`、健康检查 `/v1/health` 与卡片一致。
2. **进度只有文件级**。V1 作业的 `progress` 是 `{completed, failed, total}`（文件数），没有页级进度。
   轮询期间把文件进度报给 X01 的回调，转换完成后报 `(页数, 页数)`。
3. **middle_json 的细节**（与卡片一致的部分不重复）：块用 `content` 表达——文字类块是行内 span 列表
   （`text`/`equation_inline`/`code_inline`/`hyperlink`），容器块（list/index/image/table/chart/code）是子块列表；
   `bbox` 是 [0,1] 归一化坐标、原点在**左上**；页面尺寸在 `extensions.docvortex_layout.pages[].width_pt/height_pt`
   （只对 PDF/OFD 输出）。标题：`doc_title.level=1`、`paragraph_title.level=2..6`，实测 docx 的一级标题是
   `paragraph_title` level=2，与知识库技术方案 6.2 记的一致。
4. **Excel 的支持范围**：需求 4.1.3 说“旧格式（doc、rtf、odt、ppt、odp、xls、ods）选 MinerU 解析时直接读取”，
   但 4.1.4 的引擎表里 Excel、CSV 只有“内置表格解析”。按 4.1.4 实现：`supports()` 不含 excel、csv、
   markdown、txt（Word、PPT 的 doc/ppt/rtf/odt/odp 仍支持）。
5. **“等待 MinerU 时不占用 Docling 子进程”这条没做**。卡片要求 MinerU 在 X01 的主进程里异步执行，并给了
   “在 runner 里为远程引擎开一条单独的执行路径”的备选。X01 的结构（pool 只把作业发给子进程）不支持，
   而本卡“文件”一节没有列 `runner.py`/`pool.py`，所以没有改：MinerU 的 `parse()` 在子进程里同步执行，
   等待期间占用一个解析子进程。X01 的作业超时/取消仍然有效（超时会结束执行该作业的子进程）。建议单独开卡补。
6. **200 MiB 上限用的错误码**：`errors.py` 里没有“单文件过大”的码，用了最接近的 `ARCHIVE_TOO_LARGE`，
   说明写成“文件超过 MinerU 单文件上限 200 MiB”。平台在 nginx 和上传接口已经限制 200MB，这里是兜底。
7. **`MINERU_URL` / `MINERU_API_KEY` 的读取位置**：卡片要求这两个环境变量，但“文件”一节没有列
   `parser/app/config.py`，所以在 `engine.py` 里直接读环境变量（延迟读取，测试和运行期改环境变量都生效），
   没有加进 `Settings`。
8. **图片说明**：`image_body.content` 在 flash/txt 模式下是空的或图片文件名（如 `pptx-figure.png`），不是 OCR 文字，
   所以不作为图片说明；说明只取 `image_caption`/`image_footnote`。OCR 出来的图中文字 MinerU 会单独作为 `text` 块给出
   （已实测），会正常入库。
9. **chart 块**：卡片没提 chart。图片被判成 `chart` 时同样按图片保存（`chart_body.image_path`）；没有图片字节时
   退回 `chart_caption`。
10. **非 PDF 的页码和坐标**：`docvortex_layout` 只对 PDF/OFD 输出，其他格式的块没有 bbox。这些块仍按卡片要求
    设 `page_no = page_idx + 1`，但坐标只能是 `doctools.provenance()` 的零面积框（docling-core 的
    `ProvenanceItem.bbox` 是必填）。PPT 每页一个页码；docx 实测只有 `page_idx=0`。

## 完成记录

### 新建

- `deploy/mineru/Dockerfile`：Python 3.12.3-slim，`pip install mineru==4.0.12`（基础包：ONNX + llama.cpp，不装 PyTorch）；
  构建时 `mineru-kit models download --tier basic --small-backend onnx --source huggingface` 下载基础档模型，
  再用 `mineru-kit models verify` 校验；运行时 `MINERU_MODEL_SOURCE=local` 不联网。非 root（uid 10001）运行，
  `WORKDIR /app`（api-server 的 `--upload-dir` 默认值会检查当前目录可写，不设 WORKDIR 会启动失败）；
  装 `libgl1`、`libglib2.0-0`（DocVortex 的版面/表格后处理用 opencv）和 `curl`（健康检查）；
  启动命令 `mineru-kit api-server --host 0.0.0.0 --port 8000 --tier basic --concurrency $MINERU_CONCURRENCY --api-key $MINERU_API_KEY`。
- `parser/app/engines/mineru/client.py`：MinerU V1 客户端（httpx 同步）。上传会话（同源带密钥、不同源不带，
  相对地址按接口基地址解析后比较 scheme+host+有效端口）、提交作业（`output_formats=["zip"]`）、轮询
  （404 → `REMOTE_ERROR`“解析服务重启，请重试”）、下载并解出 ZIP（middle_json、markdown、images/）。
  单文件 200 MiB 上限。
- `parser/app/engines/mineru/convert.py`：middle_json 2.0 → DoclingDocument。标题（级别归一）、正文、列表、
  目录、表格 HTML（rowspan/colspan 用标准库 `html.parser` 解析）、图片、公式、代码、页眉页脚；
  PDF/OFD 的归一化坐标换算成 PDF 坐标系（原点左下，单位点）。
- `parser/app/engines/mineru/engine.py`：`MineruEngine`。档位解析（PDF/图片可选四档，Word/PPT/HTML/EPUB/OFD
  固定 flash）、地址选择（flash/basic 用 `MINERU_URL`，standard/advanced 用作业请求的 `remote`，
  缺 remote → `ENGINE_UNAVAILABLE`）、`availability()` 调 `/v1/health`（结果缓存 5 秒，避免 `/v1/engines` 每次打网络）。
- `parser/app/engines/mineru/__init__.py`
- `parser/tests/engines/mineru/`：`fake_server.py`（真 HTTP 的假 MinerU V1 服务，供引擎/接口测试用，
  解析子进程没法共用 `httpx.MockTransport`）、`sample_data.py`、`test_mineru_convert.py`（快照）、
  `test_mineru_client.py`（`httpx.MockTransport`）、`test_mineru_engine.py`、`test_mineru_api.py`（走 `POST /v1/jobs`
  的端到端：moto S3 + 假服务 + 真子进程）、`test_mineru_integration.py`（标记 integration）、
  `samples/`（样例文档 + MinerU 实际输出的 middle_json + images + `make_samples.py` + `refresh.py`，共 164KB）。

### 修改

- `parser/app/engines/registry.py`：`bootstrap()` 注册 `MineruEngine`。
- `parser/compose.dev.yml`：加 `mineru` 服务（宿主机 59003，`MINERU_API_KEY=adpdev`、`MINERU_CONCURRENCY=1`，
  构建上下文 `../deploy/mineru`，镜像 `adp-mineru:4.0.12`）。
- `parser/pyproject.toml`：`httpx` 从开发依赖移到运行依赖（引擎运行时要用）；`integration` 标记的说明补上 mineru。
- `parser/uv.lock`：随上面的依赖调整更新（`uv sync` 生成）。
- 本卡片的“状态”“问题”“完成记录”。

### 验收命令结果

- `make parser-lint`：`All checks passed!` / `43 files already formatted`
- `make parser-test`：`111 passed, 5 deselected, 1 warning in 24.16s`（integration 默认不跑）
- `MINERU_URL=http://127.0.0.1:59003 MINERU_API_KEY=adpdev uv run pytest -m integration`：
  `5 passed, 111 deselected, 1 warning in 2.03s`（4 个 mineru + 1 个 RustFS 冒烟）
- 快照样例用本地 mineru 4.0.12 容器（`samples/refresh.py`）生成，覆盖 PDF（标题级别、合并单元格表格、图片、
  公式、两页坐标）、docx（1/2/3 级标题、合并单元格、图片）、pptx（doc_title、列表、图片、每页一个页码）。
- 断网运行：`docker run --network none adp-mineru:4.0.12`，容器内 `mineru-kit parse page1.pdf -f zip --tier basic`
  和完整 V1 流程（上传 → 作业 → 下载 ZIP）都成功，`extensions.mineru = {tier: basic, parse_mode: txt}`。

### 资源实测（Apple M5 / arm64 / Docker 8GB，供 X10 参考）

- 镜像大小：`docker images` 显示 **3.11GB**（层：基础档模型 859MB、pip 安装 826MB、apt 216MB、基础镜像约 200MB）。
  构建约 10 分钟（含下载 819MB 模型）。
- 基础档单页 PDF（595×842，标题 + 两行正文 + 三线表 + 一张图片 + 一个公式）：预热后 **1.15s**
  （3 次 1.14 / 1.15 / 1.37s），冷启动首次 4.5s（含模型加载）。
- 内存：解析 4 次后容器峰值 **1.65GB**、常驻 **1.52GiB**（`/sys/fs/cgroup/memory.peak` 与 `memory.current`），
  远低于知识库技术方案 18 给的 8GB。
- `--concurrency` 默认 1；本轮没做并发实测（X10 负责）。

### 和卡片不一致 / 补充实现细节

接口和配置层面的差异见上面“问题”一节，这里只记实现层面的：

- 图片：从 ZIP 的 `images/` 按 `image_path` 取，取不到时退回块里的 `image_base64`（非 PDF 的独立 middle_json 会带 base64）。
- 标题级别归一：按文档里最小编号整体下移（`shift = min_level - 1`），保证最高一级标题是 1 级；
  PDF 的 `paragraph_title` 2 级 → 1 级，pptx 的 `doc_title` 1 级不动、`paragraph_title` 2 级仍是 2 级。
- 列表：每个 `list` 块建一个 list group，项开头的序号/项目符号（`- `、`1. `、`（1）`）拆出来放到 `marker`，
  避免导出成两个标记；嵌套列表拉平到同一个 group。
- 表格表头：`<thead>` 的行数作为表头行数，没有 `<thead>` 时按 1 行（与内置表格解析的默认值一致）。
- `ocr_pages`：`extensions.mineru.parse_mode == "ocr"` 时按页数计，否则 0（V1 接口没有逐页 OCR 信息）。
- `engine_version`：用 middle_json 的 `metadata.producer.version`，形如 `mineru-4.0.12`。
- 表格 HTML 用标准库 `html.parser` 解析（没有引入新依赖），能处理 PDF 表格识别输出的不带引号属性（`<td colspan=2>`）。

### 遗留

- 问题 5：要让 MinerU 等待期间不占解析子进程，需要给 X01 的 `runner.py`/`pool.py` 开一条“远程引擎在主进程异步执行”
  的路径，建议单独开一张卡。
- 问题 6：`errors.py` 没有“单文件过大”的错误码，建议后续加 `FILE_TOO_LARGE`。
- 样例里的 PDF 表格被 MinerU 的表格识别把表头整行合并成了一个跨 3 列的单元格（实际输出如此，快照按实际结果断言）；
  docx 的合并单元格是正常的两列合并。
