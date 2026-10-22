# X08 MinerU 服务镜像与适配器

状态：待开始
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

## 完成记录
