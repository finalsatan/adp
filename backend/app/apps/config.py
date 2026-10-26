"""应用配置（应用技术方案 5.1）。

全部配置存为一个 JSON，按 `app_type` 用判别联合（本阶段只有知识问答 `kb_qa`）。
保存草稿只做格式校验，所以除 `schema_version`、`app_type` 外都可缺省；发布校验
（P4-04）再检查必填项。

配置里为 `null` 的阈值在运行时取下面这些代码默认值。应用技术方案 17.1 的实测由
人做（P4-01 的占位值，标“待实测”）；实测后只改常量、不迁移数据。
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.kb.schemas import MetadataGroup, RetrievalConfig

# 待实测（应用技术方案 17.1）。
DEFAULT_MEMORY_TURNS = 5
DEFAULT_DOC_SCORE_THRESHOLD = 0.0
DEFAULT_QA_DIRECT_THRESHOLD = 0.9
DEFAULT_QA_RECALL_THRESHOLD = 0.6


class AppType(StrEnum):
    """应用类型。工作流、智能体待细化，本卡不定义。"""

    kb_qa = "kb_qa"


class AppModelConfig(BaseModel):
    """回答模型设置。`thinking` 深度思考第六期生效，第四期只保存。"""

    chat_model_id: int | None = None
    temperature: float = 0.3
    max_tokens: int | None = None
    thinking: bool = False


class AppIntentConfig(BaseModel):
    """意图识别。`model_id` 为 null 表示同回答模型。"""

    enabled: bool = True
    model_id: int | None = None


class AppRewriteConfig(BaseModel):
    """问题改写。`model_id` 为 null 表示同回答模型。"""

    enabled: bool = True
    model_id: int | None = None


class AppKbFilter(BaseModel):
    """文档库过滤条件。元数据复用 P3-01 的一层“且 / 或”，不嵌套。"""

    folder_ids: list[int] = Field(default_factory=list)
    metadata: MetadataGroup | None = None


class AppKbRef(BaseModel):
    """应用引用的一个知识库。`retrieval` 为 null 表示用知识库默认检索配置。"""

    kb_id: int
    retrieval: RetrievalConfig | None = None
    filter: AppKbFilter | None = None
    qa_category_ids: list[int] = Field(default_factory=list)


class AppRerankConfig(BaseModel):
    """文档库重排设置。"""

    enabled: bool = False
    model_id: int | None = None
    keep: int = 5


class AppDocConfig(BaseModel):
    """文档库公共检索设置。`neighbor_chunks` 相邻分块扩展第五期，第四期不生效。"""

    top_k: int = 20
    score_threshold: float | None = None
    rerank: AppRerankConfig = Field(default_factory=AppRerankConfig)
    neighbor_chunks: int = Field(default=0, ge=0, le=3)


class AppQaDirectConfig(BaseModel):
    """问答库直接回答。第六期生效，第四期只保存 / 校验。"""

    enabled: bool = True
    threshold: float | None = None


class AppQaRerankConfig(BaseModel):
    """问答库重排。第六期生效，第四期只保存 / 校验。"""

    enabled: bool = False
    model_id: int | None = None


class AppQaConfig(BaseModel):
    """问答库召回与直答。第六期生效，第四期只保存 / 校验。"""

    direct: AppQaDirectConfig = Field(default_factory=AppQaDirectConfig)
    recall_threshold: float | None = None
    recall_count: int = 3
    rerank: AppQaRerankConfig = Field(default_factory=AppQaRerankConfig)


class AppKnowledgeConfig(BaseModel):
    """知识库配置。"""

    kbs: list[AppKbRef] = Field(default_factory=list)
    doc: AppDocConfig = Field(default_factory=AppDocConfig)
    qa: AppQaConfig = Field(default_factory=AppQaConfig)


class AppReplyConfig(BaseModel):
    """回答设置。`suggestions` 追问建议第六期生效，第四期只保存。"""

    fallback_text: str = "未检索到相关知识，无法回答该问题。"
    opening: str = ""
    opening_questions: list[str] = Field(default_factory=list)
    suggestions: bool = False
    allow_download: bool = False


class KbQaAppConfig(BaseModel):
    """知识问答应用的配置。"""

    schema_version: int = 1
    app_type: Literal[AppType.kb_qa] = AppType.kb_qa
    model: AppModelConfig | None = None
    system_prompt: str = ""
    intent: AppIntentConfig = Field(default_factory=AppIntentConfig)
    rewrite: AppRewriteConfig = Field(default_factory=AppRewriteConfig)
    memory_turns: int | None = None
    knowledge: AppKnowledgeConfig = Field(default_factory=AppKnowledgeConfig)
    reply: AppReplyConfig = Field(default_factory=AppReplyConfig)


# 按 `app_type` 判别。以后加工作流、智能体时往联合里加成员。
AppConfig = Annotated[KbQaAppConfig, Field(discriminator="app_type")]


def default_config(app_type: AppType = AppType.kb_qa) -> KbQaAppConfig:
    """返回某类型的默认配置，供创建应用时序列化进 `draft_config`（P4-03）。"""
    return KbQaAppConfig(app_type=app_type)
