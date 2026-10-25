import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { client, unwrap } from "../../../api/client";
import { ApiError, isApiError } from "../../../api/errors";
import type { components, paths } from "../../../api/schema";
import {
  useDefaultSchemes,
  useMetadataFields,
  useSchemes,
  type DefaultSchemeOut,
  type MetadataField,
  type Scheme,
} from "../api";

export type DocumentOut = components["schemas"]["DocumentOut"];
export type DocumentStatus = components["schemas"]["DocumentStatus"];
export type DocumentUpdate = components["schemas"]["DocumentUpdate"];
export type DocumentBatchIn = components["schemas"]["DocumentBatchIn"];
export type DocumentBatchAction = components["schemas"]["DocumentBatchAction"];
export type DocumentBatchOut = components["schemas"]["DocumentBatchOut"];
export type DocumentPrecheckIn = components["schemas"]["DocumentPrecheckIn"];
export type DocumentPrecheckOut = components["schemas"]["DocumentPrecheckOut"];
export type DocumentUploadOut = components["schemas"]["DocumentUploadOut"];
export type DocumentRef = components["schemas"]["DocumentRef"];
export type PrecheckItem = components["schemas"]["PrecheckItem"];
export type PrecheckFile = components["schemas"]["PrecheckFile"];
export type FolderOut = components["schemas"]["FolderOut"];
export type FolderCreate = components["schemas"]["FolderCreate"];
export type ReparseMode = components["schemas"]["ReparseMode"];
export type FileType = components["schemas"]["FileType"];
export type ParseResultOut = components["schemas"]["ParseResultOut"];
export type ParseElement = components["schemas"]["ParseElement"];
export type PageDocuments = components["schemas"]["Page_DocumentOut_"];

type DocumentListQuery = NonNullable<
  paths["/api/v1/kbs/{id}/documents"]["get"]["parameters"]["query"]
>;

export { useDefaultSchemes, useMetadataFields, useSchemes };
export type { DefaultSchemeOut, MetadataField, Scheme };

// ---------------------------------------------------------------------------
// 文件类型
// ---------------------------------------------------------------------------

/** 扩展名 → 文件类型，与需求 4.1.3 的支持列表一致。 */
export const FILE_TYPE_EXTENSIONS: Record<FileType, string[]> = {
  pdf: ["pdf"],
  word: ["doc", "docx", "rtf", "odt"],
  ppt: ["ppt", "pptx", "odp"],
  excel: ["xls", "xlsx", "ods"],
  csv: ["csv", "tsv"],
  markdown: ["md"],
  txt: ["txt"],
  html: ["html", "htm", "shtml", "mhtml", "mht"],
  epub: ["epub"],
  ofd: ["ofd"],
  image: ["png", "jpg", "jpeg", "bmp", "tiff", "webp", "gif", "jp2"],
};

const EXT_TO_TYPE: Record<string, FileType> = Object.entries(FILE_TYPE_EXTENSIONS).reduce(
  (acc, [type, extensions]) => {
    for (const extension of extensions) {
      acc[extension] = type as FileType;
    }
    return acc;
  },
  {} as Record<string, FileType>,
);

/** 按扩展名取文件类型；不支持时返回 null。 */
export function fileTypeFromName(name: string): FileType | null {
  const dot = name.lastIndexOf(".");
  if (dot < 0) {
    return null;
  }
  const extension = name.slice(dot + 1).toLowerCase();
  return EXT_TO_TYPE[extension] ?? null;
}

export interface FileTypeGroup<T> {
  fileType: FileType;
  files: T[];
}

/** 按文件类型分组，保持首次出现的顺序。 */
export function groupByFileType<T extends { name: string }>(files: T[]): FileTypeGroup<T>[] {
  const groups = new Map<FileType, T[]>();
  for (const file of files) {
    const fileType = fileTypeFromName(file.name);
    if (fileType === null) {
      continue;
    }
    const list = groups.get(fileType) ?? [];
    list.push(file);
    groups.set(fileType, list);
  }
  return [...groups.entries()].map(([fileType, list]) => ({ fileType, files: list }));
}

/**
 * 某个文件类型默认选中的方案：知识库默认方案 → 平台内置通用方案 → 该类型第一个可用方案。
 */
export function resolveSchemeId(
  fileType: FileType,
  defaultSchemes: DefaultSchemeOut | undefined,
  schemes: { items: { id: number; file_type: FileType }[] } | undefined,
): number | null {
  const item = defaultSchemes?.items.find((entry) => entry.file_type === fileType);
  if (item?.scheme_id != null) {
    return item.scheme_id;
  }
  if (item?.fallback_scheme_id != null) {
    return item.fallback_scheme_id;
  }
  const first = schemes?.items.find((scheme) => scheme.file_type === fileType);
  return first?.id ?? null;
}

// ---------------------------------------------------------------------------
// 状态文案
// ---------------------------------------------------------------------------

export const PROCESSING_STATUSES: DocumentStatus[] = [
  "queued",
  "parsing",
  "chunking",
  "enhancing",
  "embedding",
];

export function isProcessing(status: DocumentStatus): boolean {
  return PROCESSING_STATUSES.includes(status);
}

/** 列表中有处理中的文档时每 5 秒轮询一次，处理完成后停止（知识库技术方案 8.1）。 */
export const DOCUMENT_POLL_INTERVAL_MS = 5000;

export function shouldPoll(docs: Pick<DocumentOut, "status">[] | undefined): boolean {
  return (docs ?? []).some((doc) => isProcessing(doc.status));
}

export interface DocumentStatusView {
  label: string;
  tagColor: string;
  error: string | null;
}

/** 文档状态文案：排队位次、向量化进度、失败原因（知识库技术方案 8.1）。 */
export function documentStatusView(
  doc: Pick<DocumentOut, "status" | "progress" | "queue_position" | "error_message">,
): DocumentStatusView {
  switch (doc.status) {
    case "queued":
      return {
        label: doc.queue_position != null ? `排队中（本库第 ${doc.queue_position} 位）` : "排队中",
        tagColor: "processing",
        error: null,
      };
    case "parsing":
      return { label: "解析中", tagColor: "processing", error: null };
    case "chunking":
      return { label: "分块中", tagColor: "processing", error: null };
    case "enhancing":
      return { label: "增强中", tagColor: "processing", error: null };
    case "embedding":
      return {
        label: `向量化中 ${Math.round(doc.progress)}%`,
        tagColor: "processing",
        error: null,
      };
    case "done":
      return { label: "已完成", tagColor: "green", error: null };
    case "failed":
      return { label: "失败", tagColor: "red", error: doc.error_message };
  }
}

/** 用定时器在存在处理中文档时轮询列表。 */
export function useDocumentPolling(processing: boolean, refetch: () => void): void {
  const refetchRef = useRef(refetch);
  refetchRef.current = refetch;

  useEffect(() => {
    if (!processing) {
      return;
    }
    const timer = window.setInterval(() => refetchRef.current(), DOCUMENT_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [processing]);
}

// ---------------------------------------------------------------------------
// 预检确认项
// ---------------------------------------------------------------------------

export type ConfirmKind = "name_conflict" | "duplicate" | "too_long";

export interface ConfirmationItem {
  kind: ConfirmKind;
  name: string;
  relativePath: string | null;
  docId: number | null;
  refName: string | null;
  item: PrecheckItem;
}

export interface PrecheckConfirmations {
  nameConflicts: ConfirmationItem[];
  duplicates: ConfirmationItem[];
  tooLong: ConfirmationItem[];
}

function toItem(kind: ConfirmKind, item: PrecheckItem, ref: DocumentRef | null): ConfirmationItem {
  return {
    kind,
    name: item.name,
    relativePath: item.relative_path,
    docId: ref?.doc_id ?? null,
    refName: ref?.name ?? null,
    item,
  };
}

/** 把预检结果拆成需要用户确认的三类：同名、内容重复、超长。 */
export function buildPrecheckConfirmations(out: DocumentPrecheckOut): PrecheckConfirmations {
  const nameConflicts: ConfirmationItem[] = [];
  const duplicates: ConfirmationItem[] = [];
  const tooLong: ConfirmationItem[] = [];

  for (const item of out.items) {
    if (item.name_conflict) {
      nameConflicts.push(toItem("name_conflict", item, item.name_conflict));
    }
    if (item.duplicate) {
      duplicates.push(toItem("duplicate", item, item.duplicate));
    }
    if (item.too_long) {
      tooLong.push(toItem("too_long", item, null));
    }
  }
  return { nameConflicts, duplicates, tooLong };
}

export interface Resolutions {
  name_conflict: Record<string, "replace" | "keep">;
  duplicate: Record<string, "skip" | "upload">;
}

/** 默认处理方式：同名替换、内容重复跳过（用户可改）。 */
export function defaultResolutions(out: DocumentPrecheckOut): Resolutions {
  const resolutions: Resolutions = { name_conflict: {}, duplicate: {} };
  for (const item of out.items) {
    if (item.name_conflict) {
      resolutions.name_conflict[item.name] = "replace";
    }
    if (item.duplicate) {
      resolutions.duplicate[item.name] = "skip";
    }
  }
  return resolutions;
}

/** 预检有硬错误（类型不支持、超过大小上限等）时不允许上传。 */
export function hasPrecheckErrors(out: DocumentPrecheckOut): boolean {
  return out.errors.length > 0;
}

// ---------------------------------------------------------------------------
// 批量操作参数
// ---------------------------------------------------------------------------

export interface BatchOptions {
  metadata?: Record<string, unknown>;
  folderId?: number | null;
  mode?: ReparseMode;
  schemeId?: number | null;
}

/** 拼装 `POST /documents:batch` 的请求体。 */
export function buildBatchPayload(
  action: DocumentBatchAction,
  docIds: number[],
  options: BatchOptions = {},
): DocumentBatchIn {
  const body: DocumentBatchIn = { action, doc_ids: docIds };
  if (action === "set_metadata") {
    body.metadata = options.metadata ?? {};
  }
  if (action === "move") {
    body.folder_id = options.folderId ?? null;
  }
  if (action === "reparse") {
    body.mode = options.mode ?? "reparse";
    body.scheme_id = options.schemeId ?? null;
  }
  return body;
}

// ---------------------------------------------------------------------------
// 列表筛选
// ---------------------------------------------------------------------------

export type DocumentStatusFilter = "all" | "failed" | "edited";

export interface MetadataCondition {
  field_id: number;
  operator: string;
  value: unknown;
}

export interface DocumentFilters {
  folderId: number | "all";
  statusFilter: DocumentStatusFilter;
  fileType: FileType | "all";
  schemeId: number | "all";
  keyword: string;
  metadata: MetadataCondition | null;
}

export interface DocumentListParams {
  folder_id?: number;
  include_sub?: boolean;
  status?: DocumentStatus;
  edited?: boolean;
  file_type?: FileType;
  scheme_id?: number;
  keyword?: string;
  metadata?: string;
  page: number;
  page_size: number;
}

/**
 * 拼装列表查询参数。
 * `edited` 不在 P3-01 契约的查询参数里，是 P3-12 卡片要求的“有人工修改”筛选（见卡片问题）。
 */
export function buildDocumentListParams(
  filters: DocumentFilters,
  page: number,
  pageSize: number,
): DocumentListParams {
  const params: DocumentListParams = { page, page_size: pageSize };
  if (filters.folderId !== "all") {
    params.folder_id = filters.folderId;
    params.include_sub = true;
  }
  if (filters.statusFilter === "failed") {
    params.status = "failed";
  } else if (filters.statusFilter === "edited") {
    params.edited = true;
  }
  if (filters.fileType !== "all") {
    params.file_type = filters.fileType;
  }
  if (filters.schemeId !== "all") {
    params.scheme_id = filters.schemeId;
  }
  if (filters.keyword) {
    params.keyword = filters.keyword;
  }
  if (filters.metadata) {
    params.metadata = JSON.stringify(filters.metadata);
  }
  return params;
}

// ---------------------------------------------------------------------------
// 查询与变更
// ---------------------------------------------------------------------------

export function useFolders(kbId: number | null) {
  return useQuery({
    queryKey: ["kb-folders", kbId],
    enabled: kbId !== null,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/kbs/{id}/folders", { params: { path: { id: kbId as number } } }),
      ),
  });
}

export function useDocuments(kbId: number | null, params: DocumentListParams) {
  return useQuery({
    queryKey: ["kb-documents", kbId, params],
    enabled: kbId !== null,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/kbs/{id}/documents", {
          params: { path: { id: kbId as number }, query: params as unknown as DocumentListQuery },
        }),
      ),
    placeholderData: (previous) => previous,
  });
}

function useInvalidateDocuments(kbId: number | null) {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["kb-documents", kbId] });
    void queryClient.invalidateQueries({ queryKey: ["kb-document"] });
    void queryClient.invalidateQueries({ queryKey: ["kb-folders", kbId] });
    if (kbId != null) {
      void queryClient.invalidateQueries({ queryKey: ["kb", kbId] });
    }
  };
}

export function useCreateFolder(kbId: number | null) {
  const invalidate = useInvalidateDocuments(kbId);
  return useMutation({
    mutationFn: async (body: FolderCreate) =>
      unwrap(
        await client.POST("/api/v1/kbs/{id}/folders", {
          params: { path: { id: kbId as number } },
          body,
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: () => invalidate(),
  });
}

export function useRenameFolder(kbId: number | null) {
  const invalidate = useInvalidateDocuments(kbId);
  return useMutation({
    mutationFn: async ({ folderId, name }: { folderId: number; name: string }) =>
      unwrap(
        await client.PATCH("/api/v1/folders/{id}", {
          params: { path: { id: folderId } },
          body: { name },
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: () => invalidate(),
  });
}

export function useDeleteFolder(kbId: number | null) {
  const invalidate = useInvalidateDocuments(kbId);
  return useMutation({
    mutationFn: async (folderId: number) =>
      unwrap(await client.DELETE("/api/v1/folders/{id}", { params: { path: { id: folderId } } })),
    meta: { skipErrorToast: true },
    onSuccess: () => invalidate(),
  });
}

export function useUpdateDocument(kbId: number | null) {
  const invalidate = useInvalidateDocuments(kbId);
  return useMutation({
    mutationFn: async ({ docId, body }: { docId: number; body: DocumentUpdate }) =>
      unwrap(
        await client.PATCH("/api/v1/documents/{id}", {
          params: { path: { id: docId } },
          body,
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: () => invalidate(),
  });
}

export function useRetryDocument(kbId: number | null) {
  const invalidate = useInvalidateDocuments(kbId);
  return useMutation({
    mutationFn: async (docId: number) =>
      unwrap(
        await client.POST("/api/v1/documents/{id}:retry", { params: { path: { id: docId } } }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: () => invalidate(),
  });
}

export function useDeleteDocument(kbId: number | null) {
  const invalidate = useInvalidateDocuments(kbId);
  return useMutation({
    mutationFn: async (docId: number) =>
      unwrap(await client.DELETE("/api/v1/documents/{id}", { params: { path: { id: docId } } })),
    meta: { skipErrorToast: true },
    onSuccess: () => invalidate(),
  });
}

export function useReparseDocument(kbId: number | null) {
  const invalidate = useInvalidateDocuments(kbId);
  return useMutation({
    mutationFn: async ({
      docId,
      mode,
      schemeId,
    }: {
      docId: number;
      mode: ReparseMode;
      schemeId: number | null;
    }) =>
      unwrap(
        await client.POST("/api/v1/documents/{id}:reparse", {
          params: { path: { id: docId } },
          body: { mode, scheme_id: schemeId },
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: () => invalidate(),
  });
}

export function useBatchDocuments(kbId: number | null) {
  const invalidate = useInvalidateDocuments(kbId);
  return useMutation({
    mutationFn: async (body: DocumentBatchIn) =>
      unwrap(await client.POST("/api/v1/documents:batch", { body })),
    meta: { skipErrorToast: true },
    onSuccess: () => invalidate(),
  });
}

/** 单个文档详情（P3-18 文档详情页）。 */
export function useDocument(docId: number | null) {
  return useQuery({
    queryKey: ["kb-document", docId],
    enabled: docId !== null,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/documents/{id}", {
          params: { path: { id: docId as number } },
        }),
      ),
  });
}

/**
 * 解析结果与元素列表。文档有人工修改时优先取修改版，没有修改版（404）时回落到原始版。
 * 文档还没有解析结果时接口返回 404，页面按状态说明处理。
 */
export function useDocumentParse(docId: number | null, preferEdited = false) {
  return useQuery({
    queryKey: ["kb-document-parse", docId, preferEdited ? "edited" : "original"],
    enabled: docId !== null,
    retry: false,
    queryFn: async (): Promise<ParseResultOut> => {
      const id = docId as number;
      if (preferEdited) {
        try {
          return await unwrap(
            await client.GET("/api/v1/documents/{id}/parse", {
              params: { path: { id }, query: { kind: "edited" } },
            }),
          );
        } catch (error) {
          if (!(isApiError(error) && error.status === 404)) {
            throw error;
          }
        }
      }
      return unwrap(
        await client.GET("/api/v1/documents/{id}/parse", {
          params: { path: { id }, query: { kind: "original" } },
        }),
      );
    },
  });
}

/** 原文件 / 预览 PDF 的读取地址（下载时带 `download=1`）。 */
export function documentFileUrl(
  docId: number,
  variant: "original" | "preview" = "original",
  download = false,
): string {
  const query = new URLSearchParams({ variant });
  if (download) {
    query.set("download", "1");
  }
  return `/api/v1/documents/${docId}/file?${query.toString()}`;
}

/** 上传前预检（同名、内容重复、超长）。 */
export async function precheckDocuments(
  kbId: number,
  body: DocumentPrecheckIn,
): Promise<DocumentPrecheckOut> {
  return unwrap(
    await client.POST("/api/v1/kbs/{id}/documents:precheck", {
      params: { path: { id: kbId } },
      body,
    }),
  );
}

/** 浏览器里算文件的 SHA-256（预检用，小写十六进制）。 */
export async function sha256File(file: File): Promise<string> {
  const buffer = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export interface UploadInput {
  file: File;
  relativePath: string;
}

export interface UploadOptions {
  kbId: number;
  files: UploadInput[];
  folderId: number | null;
  schemes: Record<string, number | null>;
  resolutions: Resolutions;
  onProgress?: (percent: number) => void;
}

/** multipart 上传（用 XHR 才能拿到进度）；上传后直接进入入库流程，不等待。 */
export function uploadDocuments(options: UploadOptions): Promise<DocumentUploadOut> {
  const formData = new FormData();
  for (const { file, relativePath } of options.files) {
    formData.append("files", file);
    formData.append("relative_paths", relativePath);
  }
  if (options.folderId != null) {
    formData.append("folder_id", String(options.folderId));
  }
  formData.append("schemes", JSON.stringify(options.schemes));
  formData.append("resolutions", JSON.stringify(options.resolutions));

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/v1/kbs/${options.kbId}/documents`);
    xhr.withCredentials = true;
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && options.onProgress) {
        options.onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as DocumentUploadOut);
        } catch {
          reject(new Error("上传响应解析失败"));
        }
        return;
      }
      let body: unknown = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // 非 JSON 响应
      }
      reject(ApiError.from(xhr.status, body));
    };
    xhr.onerror = () => reject(new Error("上传失败，请稍后再试"));
    xhr.send(formData);
  });
}
