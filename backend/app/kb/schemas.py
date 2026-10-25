"""知识库（M2）接口的枚举、请求和返回结构（P3-01 契约）。

本文件只定义结构，实现在 P3-04 起的任务卡。枚举的中文名放在本文件的字典里。
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.schemas import (
    OrgBrief,
    Page,
    RequestModel,
    ResourceStatus,
    UserBrief,
    Visibility,
)
from app.models.schemas import ModelStatus


class KbKind(StrEnum):
    """知识库类型。"""

    doc = "doc"
    qa = "qa"


class FileType(StrEnum):
    """文件类型代码，与解析服务 `parser/app/filetypes.py` 的 `FILE_TYPES` 一致。"""

    pdf = "pdf"
    word = "word"
    ppt = "ppt"
    excel = "excel"
    csv = "csv"
    markdown = "markdown"
    txt = "txt"
    html = "html"
    epub = "epub"
    ofd = "ofd"
    image = "image"


class DocumentStatus(StrEnum):
    """文档入库状态（知识库技术方案 8.1、需求 4.1.3）。"""

    queued = "queued"
    parsing = "parsing"
    chunking = "chunking"
    enhancing = "enhancing"
    embedding = "embedding"
    done = "done"
    failed = "failed"


class ParseEngine(StrEnum):
    """解析引擎。`builtin` 为内置文本 / 表格解析共用的引擎。"""

    pypdfium = "pypdfium"
    docling = "docling"
    markitdown = "markitdown"
    mineru = "mineru"
    paddleocr = "paddleocr"
    builtin = "builtin"


class ScanMode(StrEnum):
    """Docling 的扫描页判断。"""

    auto = "auto"
    all = "all"


class MineruTier(StrEnum):
    """MinerU 解析档位。"""

    flash = "flash"
    basic = "basic"
    standard = "standard"
    advanced = "advanced"


class ChunkMode(StrEnum):
    """分块方式。"""

    auto = "auto"
    heading = "heading"
    length = "length"
    rule = "rule"
    page = "page"
    whole = "whole"
    semantic = "semantic"


class SemanticSensitivity(StrEnum):
    """语义分块的断开灵敏度。"""

    low = "low"
    medium = "medium"
    high = "high"


class MetadataFieldType(StrEnum):
    """元数据字段类型。"""

    text = "text"
    number = "number"
    date = "date"
    select = "select"


class MetadataOperator(StrEnum):
    """元数据过滤操作符。"""

    eq = "eq"
    ne = "ne"
    contains = "contains"
    gt = "gt"
    gte = "gte"
    lt = "lt"
    lte = "lte"
    in_ = "in"


class ReparseMode(StrEnum):
    """重新解析方式。"""

    reparse = "reparse"
    rechunk = "rechunk"


class RetrievalMode(StrEnum):
    """检索方式。"""

    vector = "vector"
    fulltext = "fulltext"
    hybrid = "hybrid"


class DocumentBatchAction(StrEnum):
    """文档批量操作。"""

    set_metadata = "set_metadata"
    move = "move"
    enable = "enable"
    disable = "disable"
    delete = "delete"
    retry = "retry"
    reparse = "reparse"


KB_KIND_LABELS: dict[KbKind, str] = {
    KbKind.doc: "文档库",
    KbKind.qa: "问答库",
}

FILE_TYPE_LABELS: dict[FileType, str] = {
    FileType.pdf: "PDF",
    FileType.word: "Word",
    FileType.ppt: "PPT",
    FileType.excel: "Excel",
    FileType.csv: "CSV",
    FileType.markdown: "Markdown",
    FileType.txt: "TXT",
    FileType.html: "HTML",
    FileType.epub: "EPUB",
    FileType.ofd: "OFD",
    FileType.image: "图片",
}

DOCUMENT_STATUS_LABELS: dict[DocumentStatus, str] = {
    DocumentStatus.queued: "排队中",
    DocumentStatus.parsing: "解析中",
    DocumentStatus.chunking: "分块中",
    DocumentStatus.enhancing: "增强中",
    DocumentStatus.embedding: "向量化中",
    DocumentStatus.done: "已完成",
    DocumentStatus.failed: "失败",
}

PARSE_ENGINE_LABELS: dict[ParseEngine, str] = {
    ParseEngine.pypdfium: "pypdfium",
    ParseEngine.docling: "Docling",
    ParseEngine.markitdown: "MarkItDown",
    ParseEngine.mineru: "MinerU",
    ParseEngine.paddleocr: "PaddleOCR",
    ParseEngine.builtin: "内置解析",
}

CHUNK_MODE_LABELS: dict[ChunkMode, str] = {
    ChunkMode.auto: "自动",
    ChunkMode.heading: "按标题",
    ChunkMode.length: "按长度",
    ChunkMode.rule: "按规则",
    ChunkMode.page: "按页",
    ChunkMode.whole: "整篇",
    ChunkMode.semantic: "语义",
}

METADATA_FIELD_TYPE_LABELS: dict[MetadataFieldType, str] = {
    MetadataFieldType.text: "文本",
    MetadataFieldType.number: "数字",
    MetadataFieldType.date: "日期",
    MetadataFieldType.select: "单选",
}

RETRIEVAL_MODE_LABELS: dict[RetrievalMode, str] = {
    RetrievalMode.vector: "向量",
    RetrievalMode.fulltext: "全文",
    RetrievalMode.hybrid: "混合",
}


# ---- 入库方案配置（知识库技术方案 14、需求 4.1.4）----


class PreprocessConfig(RequestModel):
    """预处理规则开关。"""

    merge_whitespace: bool = True
    remove_header_footer: bool = True
    remove_links_emails: bool = False


class ParseConfig(RequestModel):
    """解析设置。按引擎带不同参数。"""

    engine: ParseEngine
    scan_mode: ScanMode | None = None
    mineru_tier: MineruTier | None = None
    preprocess: PreprocessConfig = Field(default_factory=PreprocessConfig)


class TableConfig(RequestModel):
    """表格设置。Excel / CSV 按行分块。"""

    rows_per_chunk: int = Field(default=20, ge=1, le=1000)
    header_row: int = Field(default=1, ge=0)
    sheets: Literal["all"] | list[str] = "all"


class ParentChildConfig(RequestModel):
    """父子分块设置，可叠加在任一分块方式上。"""

    enabled: bool = False
    parent_max_tokens: int = Field(default=4096, ge=1)
    child_separators: list[str] = Field(default_factory=lambda: ["\n\n", "。"])
    child_max_tokens: int = Field(default=384, ge=1)
    child_overlap_tokens: int = Field(default=40, ge=0)


class ChunkConfigBase(RequestModel):
    """分块方式的公共参数。"""

    max_tokens: int = Field(default=800, ge=1)
    min_tokens: int = Field(default=50, ge=0)
    title_path: bool = True
    parent_child: ParentChildConfig = Field(default_factory=ParentChildConfig)


class ChunkAuto(ChunkConfigBase):
    """自动：有标题按标题、PPT 按页，否则按长度。"""

    mode: Literal["auto"] = "auto"
    overlap: int = Field(default=80, ge=0)


class ChunkHeading(ChunkConfigBase):
    """按标题。"""

    mode: Literal["heading"] = "heading"
    heading_level: int = Field(default=2, ge=1, le=6)


class ChunkLength(ChunkConfigBase):
    """按长度。"""

    mode: Literal["length"] = "length"
    separators: list[str] = Field(default_factory=lambda: ["\n\n", "\n", "。", "；"])
    overlap: int = Field(default=80, ge=0)


class ChunkRule(ChunkConfigBase):
    """按规则。"""

    mode: Literal["rule"] = "rule"
    rules: list[str] = Field(default_factory=list)
    title_from_match: bool = True


class ChunkPage(ChunkConfigBase):
    """按页。"""

    mode: Literal["page"] = "page"
    pages_per_chunk: int = Field(default=1, ge=1)


class ChunkWhole(ChunkConfigBase):
    """整篇。"""

    mode: Literal["whole"] = "whole"
    max_tokens: int = Field(default=8000, ge=1)


class ChunkSemantic(ChunkConfigBase):
    """语义。"""

    mode: Literal["semantic"] = "semantic"
    sensitivity: SemanticSensitivity = SemanticSensitivity.medium
    min_tokens: int = Field(default=100, ge=0)


ChunkConfig = Annotated[
    ChunkAuto | ChunkHeading | ChunkLength | ChunkRule | ChunkPage | ChunkWhole | ChunkSemantic,
    Field(discriminator="mode"),
]


class QuestionsConfig(RequestModel):
    """假设问题增强。"""

    enabled: bool = False
    count: int = Field(default=3, ge=1)


class EnhanceConfig(RequestModel):
    """分块增强，默认全关。"""

    chat_model_id: int | None = None
    context: bool = False
    questions: QuestionsConfig = Field(default_factory=QuestionsConfig)
    summary: bool = False
    keywords: bool = False
    table_summary: bool = False


class SchemeConfig(RequestModel):
    """入库方案配置。`chunk` 是按 `mode` 区分的判别联合。"""

    schema_version: int = 1
    file_type: FileType
    parse: ParseConfig
    table: TableConfig = Field(default_factory=TableConfig)
    chunk: ChunkConfig
    enhance: EnhanceConfig = Field(default_factory=EnhanceConfig)


# ---- 知识库 ----


class KbModelOut(BaseModel):
    """知识库的向量化模型，供选择器显示。"""

    id: int
    name: str
    embedding_dim: int
    embedding_max_input: int
    status: ModelStatus


class KbOut(BaseModel):
    """知识库详情。"""

    id: int
    name: str
    description: str | None
    kind: KbKind
    org: OrgBrief
    owner: UserBrief
    visibility: Visibility
    status: ResourceStatus
    embedding_model: KbModelOut
    doc_count: int
    chunk_count: int
    created_at: datetime
    updated_at: datetime
    editable: bool
    manageable: bool


class KbCreate(RequestModel):
    """新建知识库。系统管理员必须指定 `org_id`。"""

    kind: KbKind
    name: str = Field(min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=200)
    embedding_model_id: int
    visibility: Visibility
    org_id: int | None = None


class KbUpdate(RequestModel):
    """编辑知识库。`kind` / `embedding_model_id` 只用于返回 422（实现见 P3-04）。"""

    name: str | None = Field(default=None, min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=200)
    kind: KbKind | None = None
    embedding_model_id: int | None = None


class KbRefOut(BaseModel):
    """引用知识库的资源（停用 / 删除确认弹窗用）。第三期没有应用，返回空列表。"""

    resource_id: int
    type: str
    type_label: str
    name: str
    owner: UserBrief


# ---- 检索与命中测试 ----


class RerankConfig(RequestModel):
    """重排设置。"""

    enabled: bool = False
    model_id: int | None = None
    top_n: int = Field(default=10, ge=1)


class RetrievalConfig(RequestModel):
    """检索配置。混合时两路权重之和为 1。`adjacent_n` 第三期接收但不生效。"""

    mode: RetrievalMode = RetrievalMode.hybrid
    vector_weight: float = Field(default=0.5, ge=0, le=1)
    fulltext_weight: float = Field(default=0.5, ge=0, le=1)
    top_k: int = Field(default=10, ge=1)
    score_threshold: float = Field(default=0.0, ge=0, le=1)
    rerank: RerankConfig = Field(default_factory=RerankConfig)
    adjacent_n: int = Field(default=0, ge=0, le=3)

    @model_validator(mode="after")
    def _check_weights(self) -> "RetrievalConfig":
        if (
            self.mode == RetrievalMode.hybrid
            and abs(self.vector_weight + self.fulltext_weight - 1.0) > 1e-6
        ):
            raise ValueError("混合检索时向量与全文的权重之和必须为 1")
        return self


class MetadataFilter(RequestModel):
    """元数据过滤的叶子条件。"""

    field_id: int
    operator: MetadataOperator
    value: Any


class MetadataGroup(RequestModel):
    """一层“且 / 或”多条件，不支持嵌套（2026-10-25 用户决定）。"""

    op: Literal["and", "or"]
    conditions: list[MetadataFilter] = Field(min_length=1)


class HitTestIn(RequestModel):
    """命中测试请求。`config` 临时覆盖知识库默认值，不保存。"""

    question: str = Field(min_length=1, max_length=1000)
    config: RetrievalConfig | None = None
    folder_id: int | None = None
    metadata: MetadataGroup | None = None


class HitScores(BaseModel):
    """各阶段的分数。"""

    vector: float | None = None
    keyword: float | None = None
    fusion: float | None = None
    rerank: float | None = None


class HitItem(BaseModel):
    """一条命中结果。"""

    chunk_id: int
    doc_id: int
    doc_name: str
    content: str
    title_path: str | None
    kind: str
    positions: list[Any]
    score: float
    scores: HitScores
    page_no: int | None = None
    edited: bool


class HitSteps(BaseModel):
    """按步骤的命中结果。"""

    vector: list[HitItem]
    fulltext: list[HitItem]
    fusion: list[HitItem]
    rerank: list[HitItem]


class HitTimings(BaseModel):
    """各阶段耗时（毫秒）。"""

    vector_ms: int
    fulltext_ms: int
    fusion_ms: int
    rerank_ms: int


class HitTestOut(BaseModel):
    """命中测试返回。"""

    steps: HitSteps
    final: list[HitItem]
    timings: HitTimings


# ---- 文件夹与元数据字段 ----


class FolderOut(BaseModel):
    """文件夹。`path` 为物化路径，列表按 `path` 排序。"""

    id: int
    kb_id: int
    parent_id: int | None
    path: str
    name: str
    doc_count: int


class FolderBrief(BaseModel):
    """文档列表里显示所在文件夹用。"""

    id: int
    name: str
    path: str


class FolderCreate(RequestModel):
    """新建文件夹。"""

    parent_id: int | None = None
    name: str = Field(min_length=1, max_length=50)


class FolderUpdate(RequestModel):
    """重命名文件夹。"""

    name: str = Field(min_length=1, max_length=50)


class MetadataFieldOut(BaseModel):
    """元数据字段。"""

    id: int
    kb_id: int
    name: str
    type: MetadataFieldType
    options: list[str] | None
    doc_count: int


class MetadataFieldCreate(RequestModel):
    """新建元数据字段。"""

    name: str = Field(min_length=1, max_length=50)
    type: MetadataFieldType
    options: list[str] | None = None


class MetadataFieldUpdate(RequestModel):
    """编辑元数据字段，只能改名称和选项（不允许改类型）。"""

    name: str | None = Field(default=None, min_length=1, max_length=50)
    options: list[str] | None = None


# ---- 文档 ----


class SchemeBrief(BaseModel):
    """文档使用的入库方案摘要。"""

    id: int
    name: str
    file_type: FileType
    builtin: bool


class DocumentOut(BaseModel):
    """文档详情。"""

    id: int
    kb_id: int
    folder: FolderBrief | None
    name: str
    file_type: FileType
    ext: str
    size: int
    status: DocumentStatus
    step: int | None
    progress: int
    failed_step: int | None
    error_code: str | None
    error_message: str | None
    queued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    heartbeat_at: datetime | None
    scheme: SchemeBrief | None
    scheme_snapshot_name: str | None
    current_generation: int
    metadata: dict[str, Any]
    enabled: bool
    page_count: int | None
    chunk_count: int
    enhance_partial_failed: bool
    edited: bool
    uploaded_by: UserBrief
    run_by: UserBrief
    queue_position: int | None
    created_at: datetime


class PrecheckFile(RequestModel):
    """预检的单个文件。`sha256` 由前端在浏览器里算好。"""

    name: str
    size: int
    relative_path: str | None = None
    sha256: str


class DocumentPrecheckIn(RequestModel):
    """上传预检请求。`schemes` 为文件类型代码 → 方案 ID，为空时用知识库默认方案。"""

    files: list[PrecheckFile] = Field(min_length=1)
    folder_id: int | None = None
    schemes: dict[str, int | None] = Field(default_factory=dict)


class DocumentRef(BaseModel):
    """同名 / 重复文档的引用。"""

    doc_id: int
    name: str


class PrecheckItem(BaseModel):
    """单个文件的预检结论。"""

    name: str
    relative_path: str | None
    file_type: FileType
    size: int
    scheme_id: int
    scheme_name: str
    name_conflict: DocumentRef | None
    duplicate: DocumentRef | None
    too_long: bool
    estimated_tokens: int
    limit_tokens: int


class PrecheckError(BaseModel):
    """单个文件的预检错误（类型不支持、超过大小上限等）。"""

    name: str
    code: str
    message: str


class DocumentPrecheckOut(BaseModel):
    """上传预检返回。"""

    items: list[PrecheckItem]
    errors: list[PrecheckError]


class DocumentUploadOut(BaseModel):
    """上传返回。"""

    items: list[DocumentOut]
    skipped: list["SkippedItem"]


class SkippedItem(BaseModel):
    """被跳过的文件（内容重复等）。"""

    name: str
    reason: str


class DocumentUpdate(RequestModel):
    """编辑文档：移动、设置元数据、启停。不做重命名。"""

    folder_id: int | None = None
    metadata: dict[str, Any] | None = None
    enabled: bool | None = None


class DocumentBatchIn(RequestModel):
    """文档批量操作。"""

    action: DocumentBatchAction
    doc_ids: list[int] = Field(min_length=1, max_length=100)
    metadata: dict[str, Any] | None = None
    folder_id: int | None = None
    mode: ReparseMode | None = None
    scheme_id: int | None = None


class BatchFailure(BaseModel):
    """批量操作里失败的文档。"""

    doc_id: int
    code: str
    message: str


class DocumentBatchOut(BaseModel):
    """文档批量操作返回。"""

    succeeded: list[int]
    failed: list[BatchFailure]


class ReparseIn(RequestModel):
    """重新解析请求。"""

    mode: ReparseMode
    scheme_id: int | None = None


class ReparseOut(DocumentOut):
    """重新解析返回：文档详情 + 会被人为修改覆盖的计数（P3-12 补充）。"""

    parse_edited: bool
    chunk_edited_count: int


class DocumentPage(Page[DocumentOut]):
    """文档列表：分页 + 全库状态计数（P3-12 补充，不受分页和筛选影响）。"""

    status_counts: dict[str, int]


class DocumentParseOut(BaseModel):
    """`document_parses` 的一行。"""

    kind: Literal["original", "edited"]
    doc_key: str | None
    md_key: str | None
    engine: str | None
    engine_version: str | None
    pages: int | None
    ocr_pages: int | None
    created_by: UserBrief | None
    created_at: datetime


class ParseRecordOut(BaseModel):
    """`parse_records` 的一行。"""

    id: int
    engine: str
    pages: int | None
    ocr_pages: int | None
    duration_ms: int | None
    ok: bool
    error_code: str | None
    created_at: datetime


class DocumentDetailOut(DocumentOut):
    """文档详情：在列表字段上补方案快照、两份解析结果与解析记录（P3-12 补充）。"""

    processing: bool
    parse_snapshot: dict[str, Any] | None
    parses: list[DocumentParseOut]
    parse_records: list[ParseRecordOut]


class ParseElement(BaseModel):
    """解析结果里的一个有序元素。"""

    ref: str
    type: str
    text: str
    page_no: int | None = None
    bbox: list[float] | None = None


class ParseResultOut(BaseModel):
    """解析结果：Markdown + 有序元素列表（2026-10-25 用户决定）。"""

    kind: Literal["original", "edited"]
    markdown: str
    engine: ParseEngine
    engine_version: str | None
    pages: int | None
    ocr_pages: int | None
    edited_by: UserBrief | None
    edited_at: datetime | None
    elements: list[ParseElement]


# ---- 入库方案与引擎 ----


class SchemeOut(BaseModel):
    """入库方案详情。第三期只有内置方案。"""

    id: int
    name: str
    file_type: FileType
    description: str | None
    builtin: bool
    org: OrgBrief | None
    owner: UserBrief | None
    visibility: Visibility
    status: ResourceStatus
    config: SchemeConfig
    version: int
    default_kb_count: int
    created_at: datetime
    updated_at: datetime


class EngineTier(BaseModel):
    """MinerU 的档位与可用性。"""

    tier: MineruTier
    label: str
    available: bool
    reason: str | None = None


class ParseEngineOut(BaseModel):
    """解析引擎与它支持的文件类型。"""

    name: ParseEngine
    label: str
    file_types: list[FileType]
    available: bool
    reason: str | None = None
    tiers: list[EngineTier] | None = None


class DefaultSchemeItem(BaseModel):
    """知识库按文件类型的默认方案；`fallback_*` 为未设置时平台内置的方案。"""

    file_type: FileType
    scheme_id: int | None
    scheme_name: str | None
    fallback_scheme_id: int | None
    fallback_scheme_name: str | None


class DefaultSchemeItemIn(RequestModel):
    """PUT 默认方案的单个条目。`scheme_id` 为 null 表示清除该类型的默认。"""

    file_type: FileType
    scheme_id: int | None = None


class DefaultSchemeIn(RequestModel):
    """PUT 默认方案请求，全量替换。"""

    items: list[DefaultSchemeItemIn]


class DefaultSchemeOut(BaseModel):
    """知识库默认方案返回。"""

    items: list[DefaultSchemeItem]
