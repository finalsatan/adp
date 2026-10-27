# 解析实测

把试点文档或测试样例放在单独目录中，可含子目录；脚本按扩展名递归查找支持的文件。建议输出目录放在输入目录之外，避免重复扫描。测试数据和结果不要提交。

在仓库根目录运行，例如：

```sh
export DOCLING_ARTIFACTS_PATH="$HOME/.cache/adp-docling-models"
export MINERU_URL=http://127.0.0.1:59003 MINERU_API_KEY=adpdev
make parser-bench PARSER_BENCH_ARGS='--input /tmp/samples --engines docling,docling-force,pypdfium,markitdown,mineru-flash,mineru-basic,builtin --out /tmp/bench --repeat 2'
```

Docling 模型可用 `parser/Dockerfile` 中的 `docling-tools models download` 命令下载；旧格式转换需要 X02 的 unoserver 环境（当前转换器使用 `/opt/unoserver/bin/python`；缺少该环境时结果记为 `CONVERT_FAILED`）。MinerU 快速和基础档通过 `MINERU_URL` 与 `MINERU_API_KEY` 连接服务，容器内存请用 `docker stats` 查看。

打开输出目录的 `report.html` 离线对比各引擎；`results.csv` 可用表格软件筛选，`documents/` 下保留各文档的 Markdown、JSON 和图片。重复运行时耗时取中位数，本地内存取最大峰值，结构以最后一次为准；加载时间单列。结果供人工判断质量，不自动评判。
