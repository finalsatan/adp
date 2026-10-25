"""内置入库方案、解析引擎列表与知识库默认方案（P3-06）。

第三期只有内置方案（迁移 `0004_builtin_schemes` 写入，只读）；自定义方案的增删改、
复制、导入导出与试运行是第五期。解析引擎列表复用 P3-02 的 `ParserClient`。
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    EngineUnavailableError,
    NotFoundError,
    ParserUnavailableError,
    SchemeTypeMismatchError,
    SchemeUnavailableError,
    ValidationError,
)
from app.core.schemas import (
    OrgBrief,
    Page,
    Pagination,
    ResourceStatus,
    UserBrief,
    Visibility,
)
from app.iam.context import Ctx
from app.iam.tables import Org, User
from app.kb.parser_client import ParserClient
from app.kb.schemas import (
    PARSE_ENGINE_LABELS,
    ChunkAuto,
    ChunkHeading,
    ChunkPage,
    DefaultSchemeItem,
    DefaultSchemeItemIn,
    DefaultSchemeOut,
    EngineTier,
    FileType,
    MineruTier,
    ParseConfig,
    ParseEngine,
    ParseEngineOut,
    ScanMode,
    SchemeConfig,
    SchemeOut,
)
from app.kb.tables import IngestScheme, KbDefaultScheme
from app.resources import access
from app.resources.tables import Resource

KB_TYPE = "kb"

# 引擎支持的文件类型（需求 4.1.4 的对照表）。`validate_scheme_config` 与
# 解析服务未注册的引擎都用它。
ENGINE_FILE_TYPES: dict[ParseEngine, frozenset[FileType]] = {
    ParseEngine.builtin: frozenset(
        {FileType.excel, FileType.csv, FileType.markdown, FileType.txt, FileType.html}
    ),
    ParseEngine.docling: frozenset({FileType.pdf, FileType.word, FileType.ppt, FileType.image}),
    ParseEngine.markitdown: frozenset({FileType.word, FileType.ppt}),
    ParseEngine.mineru: frozenset(
        {
            FileType.pdf,
            FileType.image,
            FileType.word,
            FileType.ppt,
            FileType.html,
            FileType.epub,
            FileType.ofd,
        }
    ),
    ParseEngine.pypdfium: frozenset({FileType.pdf}),
    ParseEngine.paddleocr: frozenset({FileType.pdf, FileType.image}),
}

MINERU_TIER_LABELS: dict[MineruTier, str] = {
    MineruTier.flash: "快速",
    MineruTier.basic: "基础",
    MineruTier.standard: "标准",
    MineruTier.advanced: "高级",
}

PARSER_UNAVAILABLE_REASON = "解析服务不可用"
ENGINE_NOT_CONFIGURED_REASON = "未配置"


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符，避免用户输入被当成模式。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---- 内置方案数据（迁移 0004 与测试共用；迁移按固定 ID 写入）----


def _builtin_config(
    file_type: FileType,
    engine: ParseEngine,
    chunk: Any,
    *,
    scan_mode: ScanMode | None = None,
    mineru_tier: MineruTier | None = None,
) -> dict[str, Any]:
    """构造一份内置方案 config；用 `SchemeConfig` 生成，保证结构与 P3-01 契约一致。"""
    scheme = SchemeConfig(
        file_type=file_type,
        parse=ParseConfig(engine=engine, scan_mode=scan_mode, mineru_tier=mineru_tier),
        chunk=chunk,
    )
    return scheme.model_dump(mode="json")


# 12 个内置方案，固定 ID；名称与文件类型照卡片表格与原型 `#v-schemes`。
# Excel / CSV 没有单独的分块方式，按行分块由 `table` 的 rows_per_chunk / header_row 表达，
# `chunk` 用自动（分块时表格不受分块方式影响，知识库技术方案 9.3）。
BUILTIN_SCHEMES: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "PDF · 通用",
        "file_type": FileType.pdf.value,
        "description": None,
        "config": _builtin_config(
            FileType.pdf, ParseEngine.docling, ChunkAuto(), scan_mode=ScanMode.auto
        ),
    },
    {
        "id": 2,
        "name": "PDF · 扫描件",
        "file_type": FileType.pdf.value,
        "description": None,
        "config": _builtin_config(
            FileType.pdf,
            ParseEngine.docling,
            ChunkHeading(heading_level=2),
            scan_mode=ScanMode.all,
        ),
    },
    {
        "id": 3,
        "name": "Word · 通用",
        "file_type": FileType.word.value,
        "description": None,
        "config": _builtin_config(FileType.word, ParseEngine.docling, ChunkAuto()),
    },
    {
        "id": 4,
        "name": "PPT · 按页",
        "file_type": FileType.ppt.value,
        "description": None,
        "config": _builtin_config(FileType.ppt, ParseEngine.docling, ChunkPage(pages_per_chunk=1)),
    },
    {
        "id": 5,
        "name": "Excel · 表格",
        "file_type": FileType.excel.value,
        "description": None,
        "config": _builtin_config(FileType.excel, ParseEngine.builtin, ChunkAuto()),
    },
    {
        "id": 6,
        "name": "CSV · 通用",
        "file_type": FileType.csv.value,
        "description": None,
        "config": _builtin_config(FileType.csv, ParseEngine.builtin, ChunkAuto()),
    },
    {
        "id": 7,
        "name": "Markdown · 通用",
        "file_type": FileType.markdown.value,
        "description": None,
        "config": _builtin_config(FileType.markdown, ParseEngine.builtin, ChunkAuto()),
    },
    {
        "id": 8,
        "name": "TXT · 通用",
        "file_type": FileType.txt.value,
        "description": None,
        "config": _builtin_config(FileType.txt, ParseEngine.builtin, ChunkAuto()),
    },
    {
        "id": 9,
        "name": "HTML · 通用",
        "file_type": FileType.html.value,
        "description": None,
        "config": _builtin_config(FileType.html, ParseEngine.builtin, ChunkAuto()),
    },
    {
        "id": 10,
        "name": "EPUB · 通用",
        "file_type": FileType.epub.value,
        "description": None,
        "config": _builtin_config(
            FileType.epub,
            ParseEngine.mineru,
            ChunkAuto(),
            mineru_tier=MineruTier.flash,
        ),
    },
    {
        "id": 11,
        "name": "OFD · 通用",
        "file_type": FileType.ofd.value,
        "description": None,
        "config": _builtin_config(
            FileType.ofd,
            ParseEngine.mineru,
            ChunkAuto(),
            mineru_tier=MineruTier.flash,
        ),
    },
    {
        "id": 12,
        "name": "图片 · OCR",
        "file_type": FileType.image.value,
        "description": None,
        "config": _builtin_config(FileType.image, ParseEngine.docling, ChunkAuto()),
    },
]


# ---- 方案读取 ----


async def scheme_out(session: AsyncSession, scheme: IngestScheme) -> SchemeOut:
    """拼 `SchemeOut`。内置方案没有资源行，`org` / `owner` 为 null。"""
    count = int(
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(KbDefaultScheme)
            .where(KbDefaultScheme.scheme_id == scheme.id)
        )
        or 0
    )
    if scheme.resource_id is None:
        org = None
        owner = None
        visibility = Visibility.platform
        status = ResourceStatus.enabled
    else:
        resource = await session.get(Resource, scheme.resource_id)
        if resource is None or resource.deleted_at is not None:
            raise NotFoundError()
        org_row = await session.get(Org, resource.org_id)
        owner_row = await session.get(User, resource.owner_id)
        org = OrgBrief(id=org_row.id, name=org_row.name) if org_row is not None else None
        owner = (
            UserBrief(id=owner_row.id, username=owner_row.username, name=owner_row.name)
            if owner_row is not None
            else None
        )
        visibility = Visibility(resource.visibility)
        status = ResourceStatus(resource.status)
    return SchemeOut(
        id=scheme.id,
        name=scheme.name,
        file_type=FileType(scheme.file_type),
        description=scheme.description,
        builtin=scheme.builtin,
        org=org,
        owner=owner,
        visibility=visibility,
        status=status,
        config=SchemeConfig.model_validate(scheme.config),
        version=scheme.version,
        default_kb_count=count,
        created_at=scheme.created_at,
        updated_at=scheme.updated_at,
    )


async def list_schemes(
    session: AsyncSession,
    file_type: FileType | None,
    builtin: bool | None,
    keyword: str | None,
    pagination: Pagination,
) -> Page[SchemeOut]:
    """方案列表；第三期只有内置方案。按 `file_type`、`name` 排序。"""
    stmt = sa.select(IngestScheme)
    if file_type is not None:
        stmt = stmt.where(IngestScheme.file_type == file_type.value)
    if builtin is not None:
        stmt = stmt.where(IngestScheme.builtin.is_(builtin))
    if keyword and keyword.strip():
        pattern = f"%{_escape_like(keyword.strip())}%"
        stmt = stmt.where(IngestScheme.name.ilike(pattern, escape="\\"))

    total = int(await session.scalar(sa.select(sa.func.count()).select_from(stmt.subquery())) or 0)
    rows = (
        (
            await session.execute(
                stmt.order_by(IngestScheme.file_type, IngestScheme.name, IngestScheme.id)
                .offset((pagination.page - 1) * pagination.page_size)
                .limit(pagination.page_size)
            )
        )
        .scalars()
        .all()
    )
    items = [await scheme_out(session, scheme) for scheme in rows]
    return Page(items=items, total=total)


async def get_scheme(session: AsyncSession, scheme_id: int) -> SchemeOut:
    scheme = await session.get(IngestScheme, scheme_id)
    if scheme is None:
        raise NotFoundError()
    return await scheme_out(session, scheme)


# ---- 校验与默认方案解析 ----


def _enhance_enabled(enhance: Any) -> bool:
    return bool(
        enhance.context
        or enhance.questions.enabled
        or enhance.summary
        or enhance.keywords
        or enhance.table_summary
    )


def validate_scheme_config(config: SchemeConfig | dict[str, Any]) -> SchemeConfig:
    """校验方案配置：结构（`SchemeConfig`）+ 引擎支持该文件类型 + 增强需选对话模型。

    第三期内置方案在迁移里已校验；这个函数给测试与第五期的自定义方案用。
    """
    try:
        parsed = config if isinstance(config, SchemeConfig) else SchemeConfig.model_validate(config)
    except PydanticValidationError as exc:
        errors = [{"loc": list(err["loc"]), "msg": err["msg"]} for err in exc.errors()]
        raise ValidationError(detail={"errors": errors}) from exc

    supported = ENGINE_FILE_TYPES.get(parsed.parse.engine, frozenset())
    if parsed.file_type not in supported:
        raise EngineUnavailableError(
            detail={
                "engine": parsed.parse.engine.value,
                "file_type": parsed.file_type.value,
            }
        )
    if _enhance_enabled(parsed.enhance) and parsed.enhance.chat_model_id is None:
        raise ValidationError(
            "开启分块增强时必须选择对话模型", detail={"field": "enhance.chat_model_id"}
        )
    return parsed


async def resolve_scheme_id(session: AsyncSession, kb_id: int, file_type: FileType) -> int | None:
    """知识库默认方案 → 该文件类型的第一个内置方案；都没有返回 None（需求 4.1.3）。"""
    default_id = await session.scalar(
        sa.select(KbDefaultScheme.scheme_id).where(
            KbDefaultScheme.kb_id == kb_id,
            KbDefaultScheme.file_type == file_type.value,
        )
    )
    if default_id is not None:
        return int(default_id)
    fallback_id = await session.scalar(
        sa.select(IngestScheme.id)
        .where(IngestScheme.file_type == file_type.value, IngestScheme.builtin.is_(True))
        .order_by(IngestScheme.id)
        .limit(1)
    )
    return int(fallback_id) if fallback_id is not None else None


# ---- 解析引擎列表 ----


def _mineru_tiers(available: bool, reason: str | None) -> list[EngineTier]:
    tiers = [
        EngineTier(
            tier=tier,
            label=MINERU_TIER_LABELS[tier],
            available=available,
            reason=reason,
        )
        for tier in (MineruTier.flash, MineruTier.basic)
    ]
    # 标准 / 高级档要系统设置里配置 GPU 或云端 API，第三期固定不可用（第六期做）。
    tiers.extend(
        EngineTier(
            tier=tier,
            label=MINERU_TIER_LABELS[tier],
            available=False,
            reason=ENGINE_NOT_CONFIGURED_REASON,
        )
        for tier in (MineruTier.standard, MineruTier.advanced)
    )
    return tiers


def _file_types_from_parser(raw: Any, engine: ParseEngine) -> list[FileType]:
    known = {ft.value: ft for ft in FileType}
    values = raw.get("file_types") if isinstance(raw, dict) else None
    if not isinstance(values, list):
        return sorted(ENGINE_FILE_TYPES[engine], key=_file_type_order)
    result = [known[value] for value in values if isinstance(value, str) and value in known]
    return result


def _file_type_order(file_type: FileType) -> int:
    return list(FileType).index(file_type)


async def list_parse_engines(client: ParserClient) -> list[ParseEngineOut]:
    """解析服务 `GET /v1/engines` 的映射；不可达时全部标不可用，不返回 502。"""
    try:
        raw = await client.list_engines()
    except ParserUnavailableError:
        raw = None

    by_name: dict[str, Any] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                by_name[item["name"]] = item

    unavailable_reason = PARSER_UNAVAILABLE_REASON if raw is None else ENGINE_NOT_CONFIGURED_REASON
    engines: list[ParseEngineOut] = []
    for engine in ParseEngine:
        info = by_name.get(engine.value)
        if info is None:
            file_types = sorted(ENGINE_FILE_TYPES[engine], key=_file_type_order)
            available = False
            reason: str | None = unavailable_reason
        else:
            file_types = _file_types_from_parser(info, engine)
            available = bool(info.get("available"))
            reason = info.get("reason")
        tiers = _mineru_tiers(available, reason) if engine == ParseEngine.mineru else None
        engines.append(
            ParseEngineOut(
                name=engine,
                label=PARSE_ENGINE_LABELS[engine],
                file_types=file_types,
                available=available,
                reason=reason,
                tiers=tiers,
            )
        )
    return engines


# ---- 知识库默认方案 ----


async def _fallback_schemes(session: AsyncSession) -> dict[str, tuple[int, str]]:
    """每个文件类型的第一个内置方案（按 ID）。"""
    rows = await session.execute(
        sa.select(IngestScheme.file_type, IngestScheme.id, IngestScheme.name)
        .where(IngestScheme.builtin.is_(True))
        .order_by(IngestScheme.id)
    )
    fallback: dict[str, tuple[int, str]] = {}
    for file_type, scheme_id, name in rows:
        fallback.setdefault(file_type, (scheme_id, name))
    return fallback


async def _explicit_schemes(session: AsyncSession, kb_id: int) -> dict[str, tuple[int, str]]:
    rows = await session.execute(
        sa.select(KbDefaultScheme.file_type, IngestScheme.id, IngestScheme.name)
        .join(IngestScheme, KbDefaultScheme.scheme_id == IngestScheme.id)
        .where(KbDefaultScheme.kb_id == kb_id)
    )
    return {file_type: (scheme_id, name) for file_type, scheme_id, name in rows}


async def get_default_schemes(session: AsyncSession, ctx: Ctx, kb_id: int) -> DefaultSchemeOut:
    """按 11 种文件类型返回知识库默认方案与平台内置兜底方案。"""
    await access.get_for(session, ctx, kb_id, access.VIEW, type=KB_TYPE)
    explicit = await _explicit_schemes(session, kb_id)
    fallback = await _fallback_schemes(session)
    items = []
    for file_type in FileType:
        chosen = explicit.get(file_type.value)
        default = fallback.get(file_type.value)
        items.append(
            DefaultSchemeItem(
                file_type=file_type,
                scheme_id=chosen[0] if chosen else None,
                scheme_name=chosen[1] if chosen else None,
                fallback_scheme_id=default[0] if default else None,
                fallback_scheme_name=default[1] if default else None,
            )
        )
    return DefaultSchemeOut(items=items)


async def _ensure_scheme_usable(
    session: AsyncSession, scheme_id: int, file_type: FileType
) -> IngestScheme:
    scheme = await session.get(IngestScheme, scheme_id)
    if scheme is None:
        raise SchemeUnavailableError()
    if scheme.file_type != file_type.value:
        raise SchemeTypeMismatchError()
    if scheme.resource_id is not None:
        resource = await session.get(Resource, scheme.resource_id)
        if resource is None or resource.deleted_at is not None:
            raise SchemeUnavailableError()
    return scheme


async def set_default_schemes(
    session: AsyncSession,
    ctx: Ctx,
    kb_id: int,
    items: list[DefaultSchemeItemIn],
) -> DefaultSchemeOut:
    """全量替换请求里出现的文件类型；`scheme_id` 为 null 表示清除该类型默认。"""
    await access.get_for(session, ctx, kb_id, access.EDIT, type=KB_TYPE)
    for item in items:
        file_type = FileType(item.file_type)
        await session.execute(
            sa.delete(KbDefaultScheme).where(
                KbDefaultScheme.kb_id == kb_id,
                KbDefaultScheme.file_type == file_type.value,
            )
        )
        if item.scheme_id is None:
            continue
        scheme = await _ensure_scheme_usable(session, item.scheme_id, file_type)
        session.add(KbDefaultScheme(kb_id=kb_id, file_type=file_type.value, scheme_id=scheme.id))
    await session.commit()
    return await get_default_schemes(session, ctx, kb_id)
