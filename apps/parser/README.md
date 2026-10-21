# ADP 独立解析服务（apps/parser）

Python HTTP 解析服务，默认端口 **8001**。只接受请求体中的文件内容或文本，**不读取客户端传入的任何磁盘路径或 URL**。当前 schema 版本为 `1`。

- 服务框架：FastAPI + uvicorn
- 格式引擎：pdfplumber/pypdf、python-docx、python-pptx、openpyxl、BeautifulSoup、轻量 Markdown/CSV/TXT 解析
- OCR/视觉：**未内置实现**；扫描 PDF 无文本层时明确失败并提示 OCR，不返回伪造结果

## 启动

从仓库根目录：

```bash
# 首次创建环境（Python 3.11+；已在 3.13 验证）
uv venv apps/parser/.venv --python 3.13
uv pip install --python apps/parser/.venv/bin/python -r apps/parser/requirements.txt

# 启动（与根目录 npm dev 脚本一致）
PARSER_TOKEN=replace-with-a-secret \
  apps/parser/.venv/bin/python -m uvicorn app:app \
  --app-dir apps/parser --host 127.0.0.1 --port 8001
```

也可以使用传统 venv：

```bash
python3 -m venv apps/parser/.venv
PIP_CACHE_DIR=/tmp/adp-pip-cache \
  apps/parser/.venv/bin/pip install -r apps/parser/requirements.txt
```

`PARSER_TOKEN` 未设置时 `/v1` 接口不鉴权；设置后所有 `/v1` 请求必须携带
`X-Parser-Token`（常量时间比较）。`/health` 始终开放，便于探活。

### 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PARSER_TOKEN` | 空 | 设置后强制校验 `X-Parser-Token` |
| `PARSER_TIMEOUT_SECONDS` | `60` | 单次解析超时，超时返回 504 |
| `PARSER_WORKERS` | `4` | 解析线程池大小 |
| `PARSER_MAX_INPUT_MB` | `20` | 解码后文件大小上限 |
| `PARSER_MAX_TEXT_CHARS` | `5000000` | 单次输出文本字符上限 |
| `PARSER_MAX_ELEMENTS` | `100000` | 单次输出元素上限 |

线程池避免阻塞事件循环；但 Python 线程无法被强制杀死，超时返回后底层线程可能继续运行到自然结束。需要硬超时应在容器/进程层再加限制。

## 接口

### `GET /health`

```json
{"status":"ok","version":"0.1.0","service":"adp-parser","schemaVersion":1,"authRequired":true}
```

### `GET /v1/engines`

返回真实可用引擎、格式、定位能力、资源上限和 OCR 可用状态。当前 `ocr.available` 为 `false`，不会把未实现能力写成可用。

### `POST /v1/parse`

请求头：`Content-Type: application/json`，配置 token 时加 `X-Parser-Token`。

二选一提交内容：

```json
{"fileName":"report.pdf","mime":"application/pdf","dataBase64":"JVBERi0xLjQK..."}
```

```json
{"fileName":"notes.md","text":"# 标题\n\n正文"}
```

- `text` 只适用于 `.txt/.md/.csv/.html`；二进制格式必须用 `dataBase64`。
- `mime` 可省略；若提供则与扩展名校验。`application/octet-stream` 视为通用类型。
- `dataBase64` 严格校验：非法字符、错误长度/填充、非规范编码位都会拒绝；允许 ASCII 空白，兼容 `data:...;base64,` 前缀。
- 返回：

```json
{
  "schemaVersion": 1,
  "text": "标题\n\n正文",
  "elements": [
    {
      "id": "e1",
      "type": "heading",
      "text": "标题",
      "level": 1,
      "page": 1,
      "row": 2,
      "paragraph": 3,
      "sheet": "Sheet1",
      "headingPath": ["标题"],
      "order": 1
    }
  ],
  "engine": "pdfplumber",
  "warnings": []
}
```

字段说明：

- `order`、`id` 从 1 开始，`order` 是阅读顺序。定位字段只在适用时出现。
- `type`：`heading`、`paragraph`、`list_item`、`code`、`table_header`、`table_row`。
- `level`：标题层级 1–9（Markdown/HTML 通常 1–6；列表缩进层级也复用该字段）。
- `page`：PDF 页码 / PPT 幻灯片页号（1 起）。
- `sheet`、`row`：Excel 工作表名与行号（1 起）；CSV 行号；表格内行号。
- `paragraph`：DOCX 正文段落序号（1 起，含空段落计数）；PPT 单页内段落序号；TXT/MD/HTML/CSV 的块序号。
- `headingPath`：当前标题路径，包含当前标题本身。PPT 每页开始重置；Excel 使用工作表名。
- 表格行 `text` 使用 `" | "` 连接单元格；表头元素为 `table_header`，后续为 `table_row`。首行表头规则见下方能力表。
- 顶层 `text` 由元素拼成：普通块之间空行，同一表格的连续行用换行连接。顶层文本不保留 Markdown 语法标记。
- `warnings` 会明确记录截断、启发式推断、跳过对象、非 UTF-8 编码、PDF 空白页等。**截断或启发式结果不会被静默伪装成完整/精确结果。**

## 真实能力与限制

| 格式 | 已实现 | 定位 | 主要限制 |
| --- | --- | --- | --- |
| PDF | pdfplumber 文本；带线框表格；pypdf 回退 | 页码、表格行号 | 标题层级仅按字号/粗体启发式推断；无 bbox；多栏阅读顺序不保证；无线框表格按文本行输出；扫描/纯图片 PDF 明确失败；加密 PDF 失败 |
| DOCX | 标题样式/大纲级别、正文、列表、表格 | 段落序号、标题路径、表格行号 | 无可靠页码（Word 分页随排版变化）；页眉页脚、文本框、嵌套表格、图片、批注未完整提取 |
| PPTX | 幻灯片标题、文本框段落、组合形状、表格 | 页号、页内段落号、表格行号 | 图表、SmartArt、演讲者备注、图片未提取（图表/备注会警告） |
| XLSX | 工作表行、首行表头、公式缓存值/公式文本 | 工作表名、行号 | 不保留样式/合并单元格结构（合并单元格只取左上值）；空工作表跳过并警告 |
| CSV | 分隔符嗅探、行解析 | 行号 | 首行默认视为表头；列数不一致只警告不修复 |
| HTML | h1–h6、段落、列表、表格、代码块 | 标题路径、表格行号 | 移除 script/style/iframe 等；CSS 隐藏文本仍可能被提取；不执行 JS；无布局信息 |
| Markdown | ATX/setext 标题、列表、代码块、GFM 表格 | 标题路径、表格行号 | 非完整 CommonMark；复杂嵌套/HTML 块有限；YAML front matter 跳过并警告 |
| TXT | 空行分段 | 段落序号 | 不推断标题层级/表格 |

PDF 标题识别是启发式：字号大于正文阈值或短粗体行会被标为标题，`warnings` 会提示“可能不准确”。PDF 表格仅对 pdfplumber 能识别的线框表格输出结构化行；稀疏/误检表格会回退为文本行并警告，避免错误切分。

### 资源与安全限制

- 解码后输入最大 **20MB**；HTTP JSON 请求体上限约为 `20MB × 4/3 + 1MB`（base64 膨胀余量）。
- Office 压缩包：最多 4096 个条目、单条目解压 100MB、总解压 200MB、压缩比 >300 且条目 >1MB 时拒绝；拒绝加密条目、路径穿越条目、XML DTD/ENTITY 声明。
- PDF 最多解析 1000 页；PPTX 最多 1000 页；XLSX 每表最多 50,000 行/512 列/250,000 单元格；CSV/Markdown 表格最多 100,000 行；输出最多 500 万字符/100,000 元素。达到上限会截断并写入 `warnings`。
- 扩展名白名单：`.txt .md .markdown .csv .html .htm .pdf .docx .pptx .xlsx`。
- 文件名只用于取扩展名，不打开、不拼接服务器路径。服务不发起任何外部 URL 请求。

### 错误

错误统一为：

```json
{"error":"scanned_pdf_no_text","message":"PDF 未包含可提取的文本层，可能是扫描件……"}
```

常见错误码：`invalid_request`、`invalid_json`、`invalid_content_type`、`invalid_file_name`、`unsupported_file_type`、`mime_mismatch`、`content_mismatch`、`invalid_zip`、`unsafe_archive`、`unsafe_xml`、`zip_limit_exceeded`、`file_too_large`、`payload_too_large`、`invalid_base64`、`empty_file`、`text_input_not_supported`、`unauthorized`、`parse_timeout`、`scanned_pdf_no_text`、`pdf_encrypted`、`no_extractable_text`、`internal_error`。

## OCR / VLM 扩展口

`adp_parser/extensions.py` 定义了同步 `OcrProvider` 协议与注册表：

```python
from adp_parser.extensions import OcrPage, register_ocr_provider

class MyProvider:
    name = "my-ocr"
    kind = "ocr"  # 或 "vision"
    def extract(self, data: bytes, file_name: str, mime: str | None) -> list[OcrPage]:
        ...

register_ocr_provider(MyProvider())
```

注册后，仅对无文本层的 PDF 页调用；返回文本会带页码写入元素，`engine` 追加 provider 名称。**仓库没有附带任何 OCR/视觉实现**；未注册时扫描 PDF 继续明确失败。`/v1/engines` 会如实报告注册表。

## 验证记录（2026-10-20，本机）

```text
$ npm run test:parser
> cd apps/parser && .venv/bin/python -m pytest tests -q
.............................                                            [100%]
# 29 passed（13 API/安全 + 5 文本格式 + 3 Office + 5 PDF + 3 真实样本）
```

另外用全新的临时 venv（`/tmp/adp-parser-verify`，Python 3.13）和 root `setup.mjs` 使用的 Python 3.12 环境分别按 `requirements.txt` + `requirements-dev.txt` 重新安装依赖，同一套 29 项测试均全部通过，确认 requirements 未漏依赖且兼容 3.12/3.13。

根目录系统集成中的解析链路也已复跑：

```text
$ node --test --test-name-pattern='独立解析服务' tests/e2e.test.mjs
✔ 独立解析服务贯通 DOCX 上传、章节表格、引用定位和原文件读取 (953ms)
# 1 pass / 0 fail
```

真实样本只读抽样（`scripts/smoke_real_datasets.py`，通过完整 HTTP handler + base64 + 线程池）：

| 样本 | 大小 | 引擎 | 耗时 | 字符/元素 | 定位/结构 | `warnings` |
| --- | ---: | --- | ---: | ---: | --- | --- |
| Massachusetts_InfoSec.docx | 97KiB | python-docx | 0.02s | 19,107 / 182 | 32 heading、1 table_header + 6 table_row、paragraph | 无 |
| SOP_52_Digital_Conductivity_Meter_Calibration.docx | 43KiB | python-docx | 0.01s | 10,740 / 89 | 78 heading、1 table_header + 1 table_row、paragraph | 无 |
| BCG_MA_HC_Collaborative_v2.pptx | 1.4MiB | python-pptx | 0.06s | 8,568 / 207 | 页 1–10、10 heading、6 table_header + 38 table_row | 图表未提取；第 11 页无文本/表格（图片/图表页） |
| RgcResources…Franchise Agreement.pdf | 102KiB | pdfplumber | 0.07s | 5,140 / 32 | 页 1–3、2 heading（启发式） | PDF 标题层级为启发式 |
| DUDE_158bfc97…pdf | 2.2MiB | pdfplumber | 0.52s | 40,773 / 161 | 页 1–20（19 页有文本） | 第 2 页无文本层；标题为启发式 |
| DUDE_0302d10b…pdf | 2.6MiB | pdfplumber | 1.38s | 8,116 / 63 | 页 1–2、表格行 | 标题为启发式 |

真实 HTTP 启动检查（`PARSER_TOKEN=test-token`）：

```text
GET  /health                         -> 200 {"status":"ok",...}
GET  /v1/engines (no token)          -> 401 unauthorized
POST /v1/parse (X-Parser-Token)      -> 200，Markdown 标题/段落与 schemaVersion=1
```

`POST /v1/parse` 的空白扫描 PDF 返回 `422 scanned_pdf_no_text`，消息明确提示 OCR；`PARSER_TOKEN` 配置后 `/v1` 无令牌返回 `401`。Docker daemon 和官方基础镜像现已就绪，但实际构建尚未完成验证：此前受镜像网络超时影响，后续构建又因自动审批审核超时而未执行。部署证据见 [MVP 验收记录](../../docs/MVP验收记录.md)。

## 测试

```bash
uv pip install --python apps/parser/.venv/bin/python -r apps/parser/requirements-dev.txt
npm run test:parser
# 或：cd apps/parser && .venv/bin/python -m pytest tests -q
```

测试会在临时内存中生成 PDF（含标题、正文、线框表格、空白扫描页、加密 PDF）、DOCX、PPTX、XLSX、TXT/MD/CSV/HTML，验证页码/工作表/行/段落定位、标题路径、表头/行、鉴权、mime/扩展名、严格 base64、压缩炸弹、DTD、超时等。`tests/test_dataset_samples.py` 在 `datasets/document-parsing` 存在时抽样真实 DOCX/PPTX/PDF，否则跳过。

真实数据抽样（只读，不修改数据目录）：

```bash
apps/parser/.venv/bin/python apps/parser/scripts/smoke_real_datasets.py
```

## 容器

```bash
docker build -t adp-parser apps/parser
docker run --rm -p 8001:8001 -e PARSER_TOKEN=replace-with-a-secret adp-parser
```

镜像以非 root 用户运行，包含 `/health` HEALTHCHECK。构建上下文为 `apps/parser`。
