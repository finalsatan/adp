# 文档解析由独立部署的自建解析服务完成

解析（文件转带结构文本、OCR、版面分析、原文定位）放在独立的 Python 服务里，参考 WeKnora DocReader 的引擎注册表、原文定位、扫描页判断等设计自建，不直接使用 DocReader 镜像，也不嵌入主服务。原因：解析依赖重（LibreOffice、Docling 模型、可选 GPU 引擎 MinerU / PaddleOCR），资源占用和故障不应影响主服务；DocReader 本身不做复杂 PDF、OCR 与分块，镜像还带 Java 等重依赖。评估见 docs/research/WeKnora-docreader评估.html。
