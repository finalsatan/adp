import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { client, unwrap } from "../../api/client";
import { isApiError } from "../../api/errors";
import type { components } from "../../api/schema";

export type Kb = components["schemas"]["KbOut"];
export type KbCreate = components["schemas"]["KbCreate"];
export type KbUpdate = components["schemas"]["KbUpdate"];
export type KbRef = components["schemas"]["KbRefOut"];
export type KbKind = components["schemas"]["KbKind"];
export type FileType = components["schemas"]["FileType"];
export type MetadataField = components["schemas"]["MetadataFieldOut"];
export type MetadataFieldCreate = components["schemas"]["MetadataFieldCreate"];
export type MetadataFieldUpdate = components["schemas"]["MetadataFieldUpdate"];
export type MetadataFieldType = components["schemas"]["MetadataFieldType"];
export type DefaultSchemeItem = components["schemas"]["DefaultSchemeItem"];
export type DefaultSchemeItemIn = components["schemas"]["DefaultSchemeItemIn"];
export type DefaultSchemeOut = components["schemas"]["DefaultSchemeOut"];
export type Scheme = components["schemas"]["SchemeOut"];
export type Member = components["schemas"]["MemberOut"];
export type Visibility = components["schemas"]["Visibility"];
export type ResourceStatus = components["schemas"]["ResourceStatus"];
export type Model = components["schemas"]["ModelOut"];

export const KB_KIND_LABELS: Record<KbKind, string> = {
  doc: "文档库",
  qa: "问答库",
};

export const FILE_TYPE_LABELS: Record<FileType, string> = {
  pdf: "PDF",
  word: "Word",
  ppt: "PPT",
  excel: "Excel",
  csv: "CSV",
  markdown: "Markdown",
  txt: "TXT",
  html: "HTML",
  epub: "EPUB",
  ofd: "OFD",
  image: "图片",
};

export const METADATA_FIELD_TYPE_LABELS: Record<MetadataFieldType, string> = {
  text: "文本",
  number: "数字",
  date: "日期",
  select: "单选",
};

export function kbKindLabel(kind: string): string {
  return KB_KIND_LABELS[kind as KbKind] ?? kind;
}

export function fileTypeLabel(fileType: string): string {
  return FILE_TYPE_LABELS[fileType as FileType] ?? fileType;
}

export function metadataFieldTypeLabel(type: string): string {
  return METADATA_FIELD_TYPE_LABELS[type as MetadataFieldType] ?? type;
}

export interface KbListParams {
  kind?: KbKind;
  visibility?: Visibility;
  org_id?: number;
  include_sub?: boolean;
  status?: ResourceStatus;
  keyword?: string;
  page?: number;
  page_size?: number;
}

export const KIND_OPTIONS: { value: KbKind | "all"; label: string }[] = [
  { value: "all", label: "全部类型" },
  { value: "doc", label: "文档库" },
  { value: "qa", label: "问答库" },
];

export const VISIBILITY_OPTIONS: { value: Visibility | "all"; label: string }[] = [
  { value: "all", label: "全部可见范围" },
  { value: "private", label: "仅自己" },
  { value: "org", label: "本组织" },
  { value: "platform", label: "全平台" },
];

export const STATUS_OPTIONS: { value: ResourceStatus | "all"; label: string }[] = [
  { value: "all", label: "全部状态" },
  { value: "enabled", label: "启用" },
  { value: "disabled", label: "已停用" },
];

export interface KbFilters {
  kind: KbKind | "all";
  visibility: Visibility | "all";
  orgId: number | undefined;
  status: ResourceStatus | "all";
  keyword: string;
}

/** 把列表筛选条件拼成接口查询参数（“全部”项不下发）。 */
export function buildKbListParams(
  filters: KbFilters,
  page: number,
  pageSize: number,
): KbListParams {
  const params: KbListParams = { page, page_size: pageSize };
  if (filters.kind !== "all") {
    params.kind = filters.kind;
  }
  if (filters.visibility !== "all") {
    params.visibility = filters.visibility;
  }
  if (filters.orgId != null) {
    params.org_id = filters.orgId;
    params.include_sub = true;
  }
  if (filters.status !== "all") {
    params.status = filters.status;
  }
  if (filters.keyword) {
    params.keyword = filters.keyword;
  }
  return params;
}

export function useKbs(params: KbListParams) {
  return useQuery({
    queryKey: ["kbs", params],
    queryFn: async () =>
      unwrap(await client.GET("/api/v1/kbs", { params: { query: params } })),
    placeholderData: (previous) => previous,
  });
}

/** 知识库详情；id 为 null 时不查询。 */
export function useKb(id: number | null) {
  return useQuery({
    queryKey: ["kb", id],
    enabled: id !== null,
    queryFn: async () =>
      unwrap(await client.GET("/api/v1/kbs/{id}", { params: { path: { id: id as number } } })),
  });
}

function useInvalidateKbs() {
  const queryClient = useQueryClient();
  return (id?: number) => {
    void queryClient.invalidateQueries({ queryKey: ["kbs"] });
    if (id != null) {
      void queryClient.invalidateQueries({ queryKey: ["kb", id] });
    }
  };
}

export function useCreateKb() {
  const invalidate = useInvalidateKbs();
  return useMutation({
    mutationFn: async (body: KbCreate) =>
      unwrap(await client.POST("/api/v1/kbs", { body })),
    meta: { skipErrorToast: true },
    onSuccess: () => invalidate(),
  });
}

export function useUpdateKb() {
  const invalidate = useInvalidateKbs();
  return useMutation({
    mutationFn: async ({ id, body }: { id: number; body: KbUpdate }) =>
      unwrap(await client.PATCH("/api/v1/kbs/{id}", { params: { path: { id } }, body })),
    meta: { skipErrorToast: true },
    onSuccess: (data) => invalidate(data.id),
  });
}

export function useDisableKb() {
  const invalidate = useInvalidateKbs();
  return useMutation({
    mutationFn: async (id: number) =>
      unwrap(await client.POST("/api/v1/kbs/{id}:disable", { params: { path: { id } } })),
    meta: { skipErrorToast: true },
    onSuccess: (data) => invalidate(data.id),
  });
}

export function useEnableKb() {
  const invalidate = useInvalidateKbs();
  return useMutation({
    mutationFn: async (id: number) =>
      unwrap(await client.POST("/api/v1/kbs/{id}:enable", { params: { path: { id } } })),
    meta: { skipErrorToast: true },
    onSuccess: (data) => invalidate(data.id),
  });
}

export function useDeleteKb() {
  const invalidate = useInvalidateKbs();
  return useMutation({
    mutationFn: async (id: number) =>
      unwrap(await client.DELETE("/api/v1/kbs/{id}", { params: { path: { id } } })),
    meta: { skipErrorToast: true },
    onSuccess: () => invalidate(),
  });
}

/** 引用该知识库的应用清单（停用 / 删除确认弹窗用）。 */
export function useKbRefs(id: number | null, enabled = true) {
  return useQuery({
    queryKey: ["kb-refs", id],
    enabled: id !== null && enabled,
    queryFn: async () =>
      unwrap(await client.GET("/api/v1/kbs/{id}/refs", { params: { path: { id: id as number } } })),
  });
}

export function useChangeKbVisibility() {
  const invalidate = useInvalidateKbs();
  return useMutation({
    mutationFn: async ({ id, visibility }: { id: number; visibility: Visibility }) =>
      unwrap(
        await client.PATCH("/api/v1/kbs/{id}/visibility", {
          params: { path: { id } },
          body: { visibility },
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: (data) => invalidate(data.id),
  });
}

export function useKbMembers(id: number | null, enabled = true) {
  return useQuery({
    queryKey: ["kb-members", id],
    enabled: id !== null && enabled,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/kbs/{id}/members", { params: { path: { id: id as number } } }),
      ),
  });
}

export function useAddKbMember() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, userId }: { id: number; userId: number }) =>
      unwrap(
        await client.POST("/api/v1/kbs/{id}/members", {
          params: { path: { id } },
          body: { user_id: userId },
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["kb-members", variables.id] });
    },
  });
}

export function useRemoveKbMember() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, userId }: { id: number; userId: number }) =>
      unwrap(
        await client.DELETE("/api/v1/kbs/{id}/members/{user_id}", {
          params: { path: { id, user_id: userId } },
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["kb-members", variables.id] });
    },
  });
}

export function useDefaultSchemes(id: number | null, enabled = true) {
  return useQuery({
    queryKey: ["kb-default-schemes", id],
    enabled: id !== null && enabled,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/kbs/{id}/default-schemes", {
          params: { path: { id: id as number } },
        }),
      ),
  });
}

export function useUpdateDefaultSchemes() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, items }: { id: number; items: DefaultSchemeItemIn[] }) =>
      unwrap(
        await client.PUT("/api/v1/kbs/{id}/default-schemes", {
          params: { path: { id } },
          body: { items },
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["kb-default-schemes", variables.id] });
    },
  });
}

export function useMetadataFields(id: number | null, enabled = true) {
  return useQuery({
    queryKey: ["kb-metadata-fields", id],
    enabled: id !== null && enabled,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/kbs/{id}/metadata-fields", {
          params: { path: { id: id as number } },
        }),
      ),
  });
}

function useInvalidateMetadataFields() {
  const queryClient = useQueryClient();
  return (id: number) => {
    void queryClient.invalidateQueries({ queryKey: ["kb-metadata-fields", id] });
  };
}

export function useCreateMetadataField() {
  const invalidate = useInvalidateMetadataFields();
  return useMutation({
    mutationFn: async ({ id, body }: { id: number; body: MetadataFieldCreate }) =>
      unwrap(
        await client.POST("/api/v1/kbs/{id}/metadata-fields", {
          params: { path: { id } },
          body,
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: (_data, variables) => invalidate(variables.id),
  });
}

export function useUpdateMetadataField() {
  const invalidate = useInvalidateMetadataFields();
  return useMutation({
    mutationFn: async ({ fieldId, body }: { kbId: number; fieldId: number; body: MetadataFieldUpdate }) =>
      unwrap(
        await client.PATCH("/api/v1/metadata-fields/{id}", {
          params: { path: { id: fieldId } },
          body,
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: (_data, variables) => invalidate(variables.kbId),
  });
}

export function useDeleteMetadataField() {
  const invalidate = useInvalidateMetadataFields();
  return useMutation({
    mutationFn: async ({ fieldId }: { kbId: number; fieldId: number }) =>
      unwrap(
        await client.DELETE("/api/v1/metadata-fields/{id}", {
          params: { path: { id: fieldId } },
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: (_data, variables) => invalidate(variables.kbId),
  });
}

/** 入库方案列表（设置页“默认入库方案”的选项）。 */
export function useSchemes(fileType?: FileType) {
  return useQuery({
    queryKey: ["schemes", fileType ?? "all"],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/schemes", {
          params: { query: { page_size: 100, file_type: fileType } },
        }),
      ),
    staleTime: 60_000,
  });
}

/** 已启用的向量化模型（新建知识库的选项）。 */
export function useEmbeddingModels() {
  return useQuery({
    queryKey: ["models", "embedding", "enabled"],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/models", {
          params: { query: { type: "embedding", status: "enabled", page_size: 100 } },
        }),
      ),
    staleTime: 60_000,
  });
}

/** 从 RESOURCE_IN_USE 错误里取出引用知识库的应用；其他错误返回空数组。 */
export function kbInUseRefs(error: unknown): KbRef[] {
  if (!isApiError(error) || error.code !== "RESOURCE_IN_USE") {
    return [];
  }
  const detail = error.detail;
  if (detail && typeof detail === "object" && "refs" in detail) {
    const refs = (detail as { refs?: unknown }).refs;
    if (Array.isArray(refs)) {
      return refs.filter(isKbRef);
    }
  }
  return [];
}

function isKbRef(value: unknown): value is KbRef {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const ref = value as Partial<KbRef>;
  return typeof ref.name === "string" && typeof ref.type === "string";
}
