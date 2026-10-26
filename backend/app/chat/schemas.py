"""对话接口的枚举、请求与返回结构（P4-01 契约）。

实现见 P4-09（会话与流式回答）、P4-10（引用预览）、P4-12（对话日志）、P4-13（反馈）。
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.schemas import OrgBrief, RequestModel, UserBrief
from app.kb.schemas import FileType

__all__ = [
    "AnswerKind",
    "AnswerStatus",
    "FeedbackRating",
    "FeedbackReason",
    "CitationType",
    "StepName",
    "CitationRender",
    "CitationOut",
    "CitationElementOut",
    "CitationPreviewOut",
    "FeedbackOut",
    "FeedbackIn",
    "TraceStepOut",
    "TraceOut",
    "AnswerOut",
    "TurnOut",
    "ConversationOut",
    "ConversationCreate",
    "ConversationUpdate",
    "AskIn",
    "DebugMessageIn",
    "DebugChatIn",
    "AppChatInfoOut",
    "LogConversationItemOut",
    "LogConversationOut",
    "FeedbackSummary",
    "FeedbackListItemOut",
    "FeedbackPage",
]


class AnswerKind(StrEnum):
    """回答类型（应用技术方案 6.4）。`qa_direct` 第六期生效。"""

    generated = "generated"
    qa_direct = "qa_direct"
    fallback = "fallback"
    chitchat = "chitchat"


class AnswerStatus(StrEnum):
    """回答状态（应用技术方案 9.2）。"""

    generating = "generating"
    done = "done"
    stopped = "stopped"
    failed = "failed"


class FeedbackRating(StrEnum):
    """点赞 / 点踩。"""

    up = "up"
    down = "down"


class FeedbackReason(StrEnum):
    """点踩原因（需求 5.4.5、原型 `#v-chat`）。"""

    wrong_answer = "wrong_answer"
    not_answered = "not_answered"
    wrong_citation = "wrong_citation"
    other = "other"


class CitationType(StrEnum):
    """引用类型。`qa` 问答对第六期生效。"""

    qa = "qa"
    doc = "doc"


class StepName(StrEnum):
    """回答流程的步骤（应用技术方案 9.1）。`suggest` 第六期生效。"""

    intent = "intent"
    rewrite = "rewrite"
    retrieve = "retrieve"
    rerank = "rerank"
    generate = "generate"
    suggest = "suggest"


class CitationRender(StrEnum):
    """引用预览的呈现方式。`table`、`image` 随知识库第五期开放，本卡不定义。"""

    pdf = "pdf"
    markdown = "markdown"
    snapshot = "snapshot"


class CitationOut(BaseModel):
    """引用快照里的一条参考内容（应用技术方案 8.2）。问答对字段第六期生效。"""

    n: int
    type: CitationType
    kb_id: int
    kb_name: str
    score: float
    # 问答对（第六期）
    qa_id: int | None = None
    question: str | None = None
    answer: str | None = None
    direct: bool | None = None
    # 文档分块
    doc_id: int | None = None
    doc_name: str | None = None
    file_type: FileType | None = None
    chunk_ids: list[int] | None = None
    generation: int | None = None
    content: str | None = None
    positions: list[Any] | None = None
    edited: bool | None = None


class CitationElementOut(BaseModel):
    """引用预览里要高亮的一个元素（复用 P3-14 的元素结构）。"""

    ref: str
    type: str
    text: str
    highlight: bool


class CitationPreviewOut(BaseModel):
    """引用预览（应用技术方案 8.3）。PDF、PPT 直接返回文件流，不走这个结构。"""

    render: CitationRender
    file_type: FileType
    kb_id: int
    kb_name: str
    doc_id: int
    doc_name: str
    page_no: int | None
    pages: list[int]
    positions: list[Any]
    content: str | None
    elements: list[CitationElementOut] | None
    generation: int
    edited: bool
    stale: bool
    note: str | None
    download_allowed: bool


class FeedbackOut(BaseModel):
    """当前用户对一条回答的反馈。"""

    id: int
    answer_id: int
    rating: FeedbackRating
    reasons: list[FeedbackReason]
    correction: str | None
    created_at: datetime
    updated_at: datetime


class FeedbackIn(RequestModel):
    """点赞 / 点踩。`reasons` 只有点踩时有意义。"""

    rating: FeedbackRating
    reasons: list[FeedbackReason] = Field(default_factory=list)
    correction: str | None = Field(default=None, max_length=2000)


class TraceStepOut(BaseModel):
    """处理过程里的一步（应用技术方案 10 的 `answer_traces.trace`）。"""

    name: StepName
    started_at: datetime
    duration_ms: int
    input: Any
    output: Any
    model_id: int | None
    model_name: str | None
    input_tokens: int | None
    output_tokens: int | None
    prompt_version: str | None


class TraceOut(BaseModel):
    """一条回答的完整处理过程。"""

    answer_id: int
    app_id: int
    created_at: datetime
    steps: list[TraceStepOut]


class AnswerOut(BaseModel):
    """一条回答。`trace` 只有日志详情返回。"""

    id: int
    turn_id: int
    seq: int
    version_no: int | None
    kind: AnswerKind
    status: AnswerStatus
    content: str
    reasoning: str
    citations: list[CitationOut]
    cited: list[int]
    suggestions: list[str]
    error_code: str | None
    error_message: str | None
    input_tokens: int | None
    output_tokens: int | None
    first_token_ms: int | None
    total_ms: int | None
    feedback: FeedbackOut | None
    trace: TraceOut | None
    created_at: datetime
    updated_at: datetime


class TurnOut(BaseModel):
    """一问及其全部回答（含重新生成前的旧回答）。"""

    id: int
    seq: int
    question: str
    rewritten_question: str | None
    current_answer_id: int
    created_at: datetime
    answers: list[AnswerOut]


class ConversationOut(BaseModel):
    """终端用户的会话。"""

    id: int
    app_id: int
    title: str
    last_active_at: datetime
    created_at: datetime


class ConversationCreate(RequestModel):
    """新建会话。`title` 不给时用“新会话”，第一问后取问题前 20 字。"""

    title: str | None = Field(default=None, min_length=1, max_length=50)


class ConversationUpdate(RequestModel):
    """重命名会话。"""

    title: str = Field(min_length=1, max_length=50)


class AskIn(RequestModel):
    """提问。"""

    question: str = Field(min_length=1, max_length=2000)


class DebugMessageIn(RequestModel):
    """调试预览里前端带上的一条历史消息。"""

    role: Literal["user", "assistant"]
    content: str


class DebugChatIn(RequestModel):
    """调试对话。`history` 最多 20 轮（40 条消息），前端带上、不保存。"""

    draft_revision: int
    question: str = Field(min_length=1, max_length=2000)
    history: list[DebugMessageIn] = Field(default_factory=list, max_length=40)


class AppChatInfoOut(BaseModel):
    """终端用户打开对话页需要的应用信息。`suggestions` 第六期生效。"""

    id: int
    name: str
    description: str | None
    opening: str
    opening_questions: list[str]
    suggestions: bool
    allow_download: bool


class LogConversationItemOut(BaseModel):
    """对话日志的会话列表项。"""

    conversation_id: int
    app_id: int
    user: UserBrief
    org: OrgBrief
    title: str
    started_at: datetime
    turn_count: int
    version_no: int | None
    has_feedback: bool
    has_down_feedback: bool
    user_deleted: bool


class LogConversationOut(BaseModel):
    """对话日志的会话详情：每问含全部回答与处理过程。"""

    conversation_id: int
    app_id: int
    user: UserBrief
    org: OrgBrief
    title: str
    created_at: datetime
    user_deleted: bool
    turns: list[TurnOut]


class FeedbackSummary(BaseModel):
    """反馈列表顶部统计（原型 `#v-appfb`）。"""

    up: int
    down: int
    down_with_correction: int


class FeedbackListItemOut(BaseModel):
    """反馈列表项。`handled` 第四期恒为 false。"""

    id: int
    answer_id: int
    conversation_id: int
    user: UserBrief
    org: OrgBrief
    question: str
    answer_excerpt: str
    rating: FeedbackRating
    reasons: list[FeedbackReason]
    correction: str | None
    handled: bool
    created_at: datetime


class FeedbackPage(BaseModel):
    """反馈列表返回。"""

    items: list[FeedbackListItemOut]
    total: int
    summary: FeedbackSummary
