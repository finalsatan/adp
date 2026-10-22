"""接口的请求和返回结构（知识库技术方案 6.1）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PreprocessOptions(BaseModel):
    merge_whitespace: bool = True
    remove_header_footer: bool = True
    remove_links_emails: bool = False


class TableOptions(BaseModel):
    rows_per_chunk: int | None = None
    header_row: int | None = None
    sheets: list[str] | None = None


class RemoteOptions(BaseModel):
    base_url: str
    api_key: str | None = None


class JobCreate(BaseModel):
    source_key: str
    file_type: str
    ext: str
    engine: str
    engine_params: dict[str, Any] = Field(default_factory=dict)
    preprocess: PreprocessOptions = Field(default_factory=PreprocessOptions)
    table: TableOptions = Field(default_factory=TableOptions)
    output_prefix: str
    images_prefix: str
    remote: RemoteOptions | None = None


class JobCreateResponse(BaseModel):
    id: str


class Progress(BaseModel):
    done_pages: int
    total_pages: int


class JobResult(BaseModel):
    doc_key: str
    md_key: str
    images_prefix: str
    pages: int
    ocr_pages: int
    empty_pages: list[int]
    engine: str
    engine_version: str
    duration_ms: int


class JobError(BaseModel):
    code: str
    message: str


class JobStatus(BaseModel):
    id: str
    status: str
    progress: Progress
    result: JobResult | None = None
    error: JobError | None = None


class EngineInfo(BaseModel):
    name: str
    file_types: list[str]
    available: bool
    reason: str | None = None
