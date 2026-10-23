"""用量统计的枚举、请求和返回结构（P2-01 契约，实现见 P2-08）。"""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel


class UsageSource(StrEnum):
    """模型调用的来源。"""

    playground = "playground"
    model_test = "model_test"
    app = "app"
    app_debug = "app_debug"
    ingest = "ingest"
    kb_hit_test = "kb_hit_test"
    scheme_trial = "scheme_trial"
    api = "api"


# 中文名放在代码里，GET /usage/meta 返回。
USAGE_SOURCE_LABELS: dict[UsageSource, str] = {
    UsageSource.playground: "模型体验",
    UsageSource.model_test: "连通测试",
    UsageSource.app: "应用",
    UsageSource.app_debug: "应用调试",
    UsageSource.ingest: "入库",
    UsageSource.kb_hit_test: "命中测试",
    UsageSource.scheme_trial: "方案试运行",
    UsageSource.api: "API",
}


class UsageGroupBy(StrEnum):
    """汇总维度。"""

    model = "model"
    org = "org"
    app = "app"
    user = "user"
    source = "source"


class UsageSortBy(StrEnum):
    """汇总排序字段。"""

    calls = "calls"
    failures = "failures"
    input_tokens = "input_tokens"
    output_tokens = "output_tokens"


class SortOrder(StrEnum):
    """排序方向。"""

    desc = "desc"
    asc = "asc"


class UsageTotals(BaseModel):
    """筛选后全部记录的合计，不受分页影响。"""

    calls: int
    failures: int
    input_tokens: int
    output_tokens: int


class UsageItem(BaseModel):
    """汇总表的一行。`key` 为分组值（空值为 null），`label` 为名称。"""

    key: str | None
    label: str
    sub_label: str | None = None
    calls: int
    failures: int
    input_tokens: int
    output_tokens: int


class UsageSummaryOut(BaseModel):
    """用量汇总返回。"""

    totals: UsageTotals
    items: list[UsageItem]
    total: int


class UsageDailyItem(BaseModel):
    """按天趋势的一天。"""

    date: date
    calls: int
    failures: int
    input_tokens: int
    output_tokens: int


class UsageDailyOut(BaseModel):
    """用量按天趋势返回。"""

    items: list[UsageDailyItem]


class UsageSourceOption(BaseModel):
    """来源代码和中文名。"""

    code: UsageSource
    label: str


class UsageMetaOut(BaseModel):
    """用量统计的元数据。"""

    sources: list[UsageSourceOption]
