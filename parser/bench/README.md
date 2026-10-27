# 解析实测

把试点文档或测试样例放在单独目录中，可含子目录；脚本按扩展名递归查找支持的文件。建议输出目录放在输入目录之外，避免重复扫描。测试数据和结果不要提交。

两种跑法：**在宿主机跑**（`make parser-bench`）和**在 parser 镜像里跑**（`make parser-bench-docker`）。输出格式一样，选哪种取决于要测什么环境。

## 在宿主机跑

```sh
export DOCLING_ARTIFACTS_PATH="$HOME/.cache/adp-docling-models"
export MINERU_URL=http://127.0.0.1:59003 MINERU_API_KEY=adpdev
make parser-bench PARSER_BENCH_ARGS='--input /tmp/samples --engines docling,docling-force,pypdfium,markitdown,mineru-flash,mineru-basic,builtin --out /tmp/bench --repeat 2'
```

Docling 模型可用 `parser/Dockerfile` 中的 `docling-tools models download` 命令下载；旧格式转换需要 X02 的 unoserver 环境（当前转换器使用 `/opt/unoserver/bin/python`；缺少该环境时结果记为 `CONVERT_FAILED`）。MinerU 快速和基础档通过 `MINERU_URL` 与 `MINERU_API_KEY` 连接服务，容器内存请用 `docker stats` 查看。

## 在 parser 镜像里跑

宿主机不用装 LibreOffice、中文字体和 Docling 模型；doc、ppt、xls 也能真转换。用和 `make parser-test-docker` 同一个镜像（`adp-parser:test`，`--target test`）：

```sh
make parser-bench-docker \
  PARSER_BENCH_INPUT=/tmp/samples \
  PARSER_BENCH_OUT=/tmp/bench \
  PARSER_BENCH_ENGINES=docling,docling-force,pypdfium,markitdown,mineru-flash,mineru-basic,builtin \
  PARSER_BENCH_REPEAT=1
```

- `PARSER_BENCH_INPUT`（必填）：宿主机上的输入目录，以只读方式挂到容器 `/bench/input`。
- `PARSER_BENCH_OUT`（必填）：宿主机上的输出目录，挂到容器 `/bench/out`；不要放在仓库里。容器用 `--user $(id -u):$(id -g)` 运行，生成的文件宿主机当前用户能直接读写。
- `PARSER_BENCH_ENGINES`：默认 `docling,pypdfium,markitdown,builtin`。
- `PARSER_BENCH_REPEAT`：默认 `1`。
- `PARSER_BENCH_IMAGE`：默认 `adp-parser:test`；镜像不存在时先 `make parser-test-docker` 构建。
- 只选本地引擎时容器断网运行（`--network none`），本地引擎和旧格式转换都不需要外网。
- 选了 `mineru-flash` / `mineru-basic` 时容器改用默认网络，通过 `host.docker.internal` 访问宿主机上的 mineru 服务（默认 `MINERU_URL=http://host.docker.internal:59003`、`MINERU_API_KEY=adpdev`，与 `parser/compose.dev.yml` 一致；Linux 上额外加 `--add-host host.docker.internal:host-gateway`）。这两个值可以用同名环境变量覆盖。

## 两种跑法的区别

| | 宿主机跑 | 镜像里跑 |
| --- | --- | --- |
| 前置环境 | 要自己准备 Docling 模型、LibreOffice/unoserver、中文字体 | 镜像里都有，宿主机只要 Docker |
| 旧格式（doc、ppt、xls） | 缺 unoserver 时记 `CONVERT_FAILED` | 能真转换，`conversion_s` 有值 |
| 耗时和内存 | 宿主机（macOS/Linux、原生）的数字 | Linux 容器（CPU、`--network none`）的数字，更接近生产 |
| MinerU 地址 | `127.0.0.1:59003` | `host.docker.internal:59003` |
| 网络 | 宿主机网络 | 只选本地引擎时断网，便于验证离线可用 |

两边都用同一份 `bench/run.py`、同一套引擎代码和统一后处理；本地引擎的耗时和内存都在独立子进程里量，量法相同。

## 看结果

打开输出目录的 `report.html` 离线对比各引擎；`results.csv` 可用表格软件筛选，`documents/` 下保留各文档的 Markdown、JSON 和图片。重复运行时耗时取中位数，本地内存取最大峰值，结构以最后一次为准；加载时间单列。标题统计中的 `title` 表示文档标题，数字键表示对应层级的章节标题，例如 `{"title": 1, "1": 2, "2": 3}`。旧格式在一次运行中按文件转换一次，同文件需要转换的引擎共用结果；转换耗时单列，不计入引擎内存峰值。结果供人工判断质量，不自动评判。

本地引擎的内存峰值取子进程的 `RUSAGE_CHILDREN` 最大值（Linux 容器里是 KB 换算成 MB，macOS 上是字节换算），因此它包含子进程启动和模型预热的开销，是「一个解析进程」的上限，不是增量。MinerU 在远端容器里跑，内存在报告里注明「见 mineru 容器」。
