import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";

import { client, unwrap } from "../../api/client";
import { ApiError } from "../../api/errors";
import type { components } from "../../api/schema";

export type App = components["schemas"]["AppOut"];
export type AppCreate = components["schemas"]["AppCreate"];
export type AppUpdate = components["schemas"]["AppUpdate"];
export type AppType = components["schemas"]["AppType"];
export type AppStatus = components["schemas"]["AppStatus"];
export type AppVersionBrief = components["schemas"]["AppVersionBrief"];
export type UsageScopeOut = components["schemas"]["UsageScopeOut"];
export type UsageScopeIn = components["schemas"]["UsageScopeIn"];
export type UsageScopeType = components["schemas"]["UsageScopeType"];
export type Visibility = components["schemas"]["Visibility"];
export type Member = components["schemas"]["MemberOut"];
export type UserSearch = components["schemas"]["UserSearchOut"];

export const APP_TYPE_LABELS: Record<AppType, string> = {
  kb_qa: "知识问答",
};

export const APP_STATUS_LABELS: Record<AppStatus, string> = {
  unpublished: "未发布",
  published: "已发布",
  disabled: "已停用",
};

export const USAGE_SCOPE_LABELS: Record<UsageScopeType, string> = {
  platform: "全平台",
  orgs: "指定组织",
  users: "指定用户",
};

export function appTypeLabel(type: string): string {
  return APP_TYPE_LABELS[type as AppType] ?? type;
}

export function appStatusLabel(status: string): string {
  return APP_STATUS_LABELS[status as AppStatus] ?? status;
}

export function usageScopeLabel(scope: string): string {
  return USAGE_SCOPE_LABELS[scope as UsageScopeType] ?? scope;
}

/** 当前发布版本的显示，如 v3 · 10-21。 */
export function versionLabel(version: AppVersionBrief | null): string {
  if (!version) {
    return "—";
  }
  return `v${version.version_no} · ${dayjs(version.published_at).format("MM-DD")}`;
}

/** 列表、页头里使用范围的中文展示。 */
export function usageScopeText(scope: UsageScopeOut): string {
  switch (scope.scope) {
    case "platform":
      return "全平台";
    case "orgs":
      return scope.orgs.map((org) => org.name).join("、");
    case "users":
      return scope.users.map((user) => user.name).join("、");
  }
}

// ---- P4-17：对话日志与用户反馈 ----

export type LogConversationItemOut = components["schemas"]["LogConversationItemOut"];
export type LogConversationOut = components["schemas"]["LogConversationOut"];
export type LogTurnOut = components["schemas"]["TurnOut"];
export type LogAnswerOut = components["schemas"]["AnswerOut"];
export type LogTraceOut = components["schemas"]["TraceOut"];
export type LogTraceStepOut = components["schemas"]["TraceStepOut"];
export type LogCitationPreviewOut = components["schemas"]["CitationPreviewOut"];
export type FeedbackListItemOut = components["schemas"]["FeedbackListItemOut"];
export type FeedbackPageOut = components["schemas"]["FeedbackPage"];
export type FeedbackRatingValue = components["schemas"]["FeedbackRating"];
export type FeedbackReasonValue = components["schemas"]["FeedbackReason"];
export type LogRange = "7d" | "30d" | "all";
export type LogFeedbackFilter = "all" | "up" | "down";

export const LOG_RANGE_OPTIONS: { value: LogRange; label: string }[] = [
  { value: "7d", label: "最近 7 天" },
  { value: "30d", label: "最近 30 天" },
  { value: "all", label: "全部时间" },
];

/** 日志会话列表的反馈筛选：本期按卡片只提供“全部 / 有点踩”。 */
export const LOG_FEEDBACK_OPTIONS: { value: LogFeedbackFilter; label: string }[] = [
  { value: "all", label: "全部反馈" },
  { value: "down", label: "有点踩" },
];

export const FEEDBACK_RATING_OPTIONS: { value: "all" | FeedbackRatingValue; label: string }[] = [
  { value: "all", label: "全部反馈" },
  { value: "up", label: "点赞" },
  { value: "down", label: "点踩" },
];

/** 状态筛选：本期 `handled` 恒为 false，只提供“全部 / 未处理”。 */
export const FEEDBACK_STATUS_OPTIONS: { value: "all" | "unhandled"; label: string }[] = [
  { value: "all", label: "全部状态" },
  { value: "unhandled", label: "未处理" },
];

export const FEEDBACK_REASON_LABELS: Record<FeedbackReasonValue, string> = {
  wrong_answer: "答案错误",
  not_answered: "没有回答问题",
  wrong_citation: "引用不对",
  other: "其他",
};

export function feedbackReasonLabel(reason: string): string {
  return FEEDBACK_REASON_LABELS[reason as FeedbackReasonValue] ?? reason;
}

export interface LogListParams {
  range?: LogRange;
  feedback?: LogFeedbackFilter;
  keyword?: string | null;
  page?: number;
  page_size?: number;
}

/** 拼对话日志会话列表的查询参数：时间、反馈筛选与分页；关键词为空不下发。 */
export function buildLogListParams(
  range: LogRange,
  feedback: LogFeedbackFilter,
  page: number,
  pageSize: number,
  keyword = "",
): LogListParams {
  const params: LogListParams = { range, feedback, page, page_size: pageSize };
  const trimmed = keyword.trim();
  if (trimmed.length > 0) {
    params.keyword = trimmed;
  }
  return params;
}

export interface FeedbackFilters {
  rating: "all" | FeedbackRatingValue;
  hasCorrection: boolean;
  status: "all" | "unhandled";
  range: LogRange;
}

export interface FeedbackListParams {
  rating?: FeedbackRatingValue | null;
  has_correction?: boolean | null;
  handled?: boolean | null;
  range?: LogRange;
  page?: number;
  page_size?: number;
}

/** 拼反馈列表的查询参数：“全部”和未勾选项不下发；状态本期只有未处理（handled=false）。 */
export function buildFeedbackListParams(
  filters: FeedbackFilters,
  page: number,
  pageSize: number,
): FeedbackListParams {
  const params: FeedbackListParams = { range: filters.range, page, page_size: pageSize };
  if (filters.rating !== "all") {
    params.rating = filters.rating;
  }
  if (filters.hasCorrection) {
    params.has_correction = true;
  }
  if (filters.status === "unhandled") {
    params.handled = false;
  }
  return params;
}

/** 反馈列表里“对话日志”跳转的地址，带会话参数供日志页定位。 */
export function logConversationPath(appId: number, conversationId: number): string {
  return `/apps/${appId}/logs?conv=${conversationId}`;
}

/** 403 的权限错误（仅“查看”权限读日志/反馈时后端返回）。 */
export function isForbidden(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { status?: unknown }).status === 403
  );
}

export function useLogConversations(appId: number | null, params: LogListParams) {
  return useQuery({
    queryKey: ["app-logs", appId, params],
    enabled: appId !== null,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/apps/{id}/logs/conversations", {
          params: { path: { id: appId as number }, query: params },
        }),
      ),
    placeholderData: (previous) => previous,
  });
}

/** 会话详情；appId 或 conversationId 为 null 时不查询。 */
export function useLogConversation(appId: number | null, conversationId: number | null) {
  return useQuery({
    queryKey: ["app-log", appId, conversationId],
    enabled: appId !== null && conversationId !== null,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/apps/{id}/logs/conversations/{conv}", {
          params: { path: { id: appId as number, conv: conversationId as number } },
        }),
      ),
  });
}

export function useAppFeedback(appId: number | null, params: FeedbackListParams) {
  return useQuery({
    queryKey: ["app-feedback", appId, params],
    enabled: appId !== null,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/apps/{id}/feedback", {
          params: { path: { id: appId as number }, query: params },
        }),
      ),
    placeholderData: (previous) => previous,
  });
}

export interface AppListParams {
  app_type?: AppType;
  status?: AppStatus;
  visibility?: Visibility;
  org_id?: number;
  include_sub?: boolean;
  keyword?: string;
  page?: number;
  page_size?: number;
}

export const APP_TYPE_OPTIONS: { value: AppType | "all"; label: string }[] = [
  { value: "all", label: "全部类型" },
  { value: "kb_qa", label: "知识问答" },
];

export const APP_STATUS_OPTIONS: { value: AppStatus | "all"; label: string }[] = [
  { value: "all", label: "全部发布状态" },
  { value: "unpublished", label: "未发布" },
  { value: "published", label: "已发布" },
  { value: "disabled", label: "已停用" },
];

export const APP_VISIBILITY_OPTIONS: { value: Visibility | "all"; label: string }[] = [
  { value: "all", label: "全部可见范围" },
  { value: "private", label: "仅自己" },
  { value: "org", label: "本组织" },
  { value: "platform", label: "全平台" },
];

/** 可见范围选项（弹窗、设置页用，不带“全部”）。 */
export const VISIBILITY_OPTIONS: { value: Visibility; label: string }[] = [
  { value: "private", label: "仅自己" },
  { value: "org", label: "本组织" },
  { value: "platform", label: "全平台" },
];

/** 使用范围三档及说明（原型 #v-appperm）。 */
export const USAGE_SCOPE_OPTIONS: {
  value: UsageScopeType;
  label: string;
  description: string;
}[] = [
  { value: "platform", label: "全平台", description: "所有登录用户都能使用" },
  { value: "orgs", label: "指定组织", description: "所选组织及其下级组织的用户能使用" },
  { value: "users", label: "指定用户", description: "只有所选用户能使用" },
];

/** 使用范围三档提交给接口的结构：非当前档的名单置空。 */
export function buildUsageScopeIn(
  scope: UsageScopeType,
  orgIds: number[],
  userIds: number[],
): UsageScopeIn {
  return {
    scope,
    org_ids: scope === "orgs" ? orgIds : [],
    user_ids: scope === "users" ? userIds : [],
  };
}

export interface AppFilters {
  appType: AppType | "all";
  status: AppStatus | "all";
  visibility: Visibility | "all";
  orgId: number | undefined;
  keyword: string;
}

/** 把列表筛选条件拼成接口查询参数（“全部”项不下发）。 */
export function buildAppListParams(
  filters: AppFilters,
  page: number,
  pageSize: number,
): AppListParams {
  const params: AppListParams = { page, page_size: pageSize };
  if (filters.appType !== "all") {
    params.app_type = filters.appType;
  }
  if (filters.status !== "all") {
    params.status = filters.status;
  }
  if (filters.visibility !== "all") {
    params.visibility = filters.visibility;
  }
  if (filters.orgId != null) {
    params.org_id = filters.orgId;
    params.include_sub = true;
  }
  if (filters.keyword) {
    params.keyword = filters.keyword;
  }
  return params;
}

export function useApps(params: AppListParams) {
  return useQuery({
    queryKey: ["apps", params],
    queryFn: async () =>
      unwrap(await client.GET("/api/v1/apps", { params: { query: params } })),
    placeholderData: (previous) => previous,
  });
}

/** 应用详情；id 为 null 时不查询。 */
export function useApp(id: number | null) {
  return useQuery({
    queryKey: ["app", id],
    enabled: id !== null,
    queryFn: async () =>
      unwrap(await client.GET("/api/v1/apps/{id}", { params: { path: { id: id as number } } })),
  });
}

function useInvalidateApps() {
  const queryClient = useQueryClient();
  return (id?: number) => {
    void queryClient.invalidateQueries({ queryKey: ["apps"] });
    if (id != null) {
      void queryClient.invalidateQueries({ queryKey: ["app", id] });
    }
  };
}

export function useCreateApp() {
  const invalidate = useInvalidateApps();
  return useMutation({
    mutationFn: async (body: AppCreate) =>
      unwrap(await client.POST("/api/v1/apps", { body })),
    meta: { skipErrorToast: true },
    onSuccess: () => invalidate(),
  });
}

export function useUpdateApp() {
  const invalidate = useInvalidateApps();
  return useMutation({
    mutationFn: async ({ id, body }: { id: number; body: AppUpdate }) =>
      unwrap(await client.PATCH("/api/v1/apps/{id}", { params: { path: { id } }, body })),
    meta: { skipErrorToast: true },
    onSuccess: (data) => invalidate(data.id),
  });
}

export function useDisableApp() {
  const invalidate = useInvalidateApps();
  return useMutation({
    mutationFn: async (id: number) =>
      unwrap(await client.POST("/api/v1/apps/{id}:disable", { params: { path: { id } } })),
    meta: { skipErrorToast: true },
    onSuccess: (data) => invalidate(data.id),
  });
}

export function useEnableApp() {
  const invalidate = useInvalidateApps();
  return useMutation({
    mutationFn: async (id: number) =>
      unwrap(await client.POST("/api/v1/apps/{id}:enable", { params: { path: { id } } })),
    meta: { skipErrorToast: true },
    onSuccess: (data) => invalidate(data.id),
  });
}

export function useDeleteApp() {
  const invalidate = useInvalidateApps();
  return useMutation({
    mutationFn: async (id: number) =>
      unwrap(await client.DELETE("/api/v1/apps/{id}", { params: { path: { id } } })),
    meta: { skipErrorToast: true },
    onSuccess: () => invalidate(),
  });
}

export function useChangeAppVisibility() {
  const invalidate = useInvalidateApps();
  return useMutation({
    mutationFn: async ({ id, visibility }: { id: number; visibility: Visibility }) =>
      unwrap(
        await client.PATCH("/api/v1/apps/{id}/visibility", {
          params: { path: { id } },
          body: { visibility },
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: (data) => invalidate(data.id),
  });
}

/** 应用的使用范围；id 为 null 时不查询。 */
export function useAppUsageScope(id: number | null, enabled = true) {
  return useQuery({
    queryKey: ["app-usage-scope", id],
    enabled: id !== null && enabled,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/apps/{id}/usage-scope", {
          params: { path: { id: id as number } },
        }),
      ),
  });
}

export function useUpdateAppUsageScope() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, body }: { id: number; body: UsageScopeIn }) =>
      unwrap(
        await client.PUT("/api/v1/apps/{id}/usage-scope", {
          params: { path: { id } },
          body,
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: (data, variables) => {
      queryClient.setQueryData(["app-usage-scope", variables.id], data);
      void queryClient.invalidateQueries({ queryKey: ["app", variables.id] });
    },
  });
}

export function useAppMembers(id: number | null, enabled = true) {
  return useQuery({
    queryKey: ["app-members", id],
    enabled: id !== null && enabled,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/apps/{id}/members", {
          params: { path: { id: id as number } },
        }),
      ),
  });
}

export function useAddAppMember() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, userId }: { id: number; userId: number }) =>
      unwrap(
        await client.POST("/api/v1/apps/{id}/members", {
          params: { path: { id } },
          body: { user_id: userId },
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["app-members", variables.id] });
    },
  });
}

export function useRemoveAppMember() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, userId }: { id: number; userId: number }) =>
      unwrap(
        await client.DELETE("/api/v1/apps/{id}/members/{user_id}", {
          params: { path: { id, user_id: userId } },
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["app-members", variables.id] });
    },
  });
}

// ---- P4-15：配置、草稿、发布历史、候选知识库、调试 ----

export type KbQaAppConfig = components["schemas"]["KbQaAppConfig"];
export type AppModelConfig = components["schemas"]["AppModelConfig"];
export type AppIntentConfig = components["schemas"]["AppIntentConfig"];
export type AppRewriteConfig = components["schemas"]["AppRewriteConfig"];
export type AppKnowledgeConfig = components["schemas"]["AppKnowledgeConfig"];
export type AppKbRef = components["schemas"]["AppKbRef"];
export type AppKbFilter = components["schemas"]["AppKbFilter"];
export type AppDocConfig = components["schemas"]["AppDocConfig"];
export type AppRerankConfig = components["schemas"]["AppRerankConfig"];
export type AppQaConfig = components["schemas"]["AppQaConfig"];
export type AppQaDirectConfig = components["schemas"]["AppQaDirectConfig"];
export type AppQaRerankConfig = components["schemas"]["AppQaRerankConfig"];
export type AppReplyConfig = components["schemas"]["AppReplyConfig"];
export type RetrievalConfig = components["schemas"]["RetrievalConfig"];
export type RetrievalMode = components["schemas"]["RetrievalMode"];
export type MetadataGroup = components["schemas"]["MetadataGroup"];
export type MetadataFilter = components["schemas"]["MetadataFilter"];
export type MetadataOperator = components["schemas"]["MetadataOperator"];
export type DraftIn = components["schemas"]["DraftIn"];
export type DraftOut = components["schemas"]["DraftOut"];
export type AppVersionOut = components["schemas"]["AppVersionOut"];
export type PublishIn = components["schemas"]["PublishIn"];
export type KbCandidateOut = components["schemas"]["KbCandidateOut"];
export type PromptPreviewOut = components["schemas"]["PromptPreviewOut"];
export type DebugChatIn = components["schemas"]["DebugChatIn"];
export type DebugMessageIn = components["schemas"]["DebugMessageIn"];
export type CitationOut = components["schemas"]["CitationOut"];
export type TraceOut = components["schemas"]["TraceOut"];
export type TraceStepOut = components["schemas"]["TraceStepOut"];
export type StepName = components["schemas"]["StepName"];
export type CitationPreviewOut = components["schemas"]["CitationPreviewOut"];
export type AppModel = components["schemas"]["ModelOut"];

/** 发布校验问题（`PUBLISH_CHECK_FAILED` 的 `detail.problems`）。 */
export interface PublishProblem {
  code: string;
  message: string;
  field?: string | null;
}

/** 表单里的配置：把契约里可缺省的嵌套结构补成必填，字段为 `null` 表示用代码默认值。 */
export interface AppConfigForm {
  schema_version: number;
  app_type: "kb_qa";
  model: AppModelConfig;
  system_prompt: string;
  intent: AppIntentConfig;
  rewrite: AppRewriteConfig;
  memory_turns: number | null;
  knowledge: {
    kbs: AppKbRef[];
    doc: Omit<AppDocConfig, "rerank"> & { rerank: AppRerankConfig };
    qa: Omit<AppQaConfig, "direct" | "rerank"> & {
      direct: AppQaDirectConfig;
      rerank: AppQaRerankConfig;
    };
  };
  reply: AppReplyConfig;
}

const DEFAULT_MODEL: AppModelConfig = {
  chat_model_id: null,
  temperature: 0.3,
  max_tokens: null,
  thinking: false,
};
const DEFAULT_INTENT: AppIntentConfig = { enabled: true, model_id: null };
const DEFAULT_REWRITE: AppRewriteConfig = { enabled: true, model_id: null };
const DEFAULT_DOC: AppConfigForm["knowledge"]["doc"] = {
  top_k: 20,
  score_threshold: null,
  rerank: { enabled: false, model_id: null, keep: 5 },
  neighbor_chunks: 0,
};
const DEFAULT_QA: AppConfigForm["knowledge"]["qa"] = {
  direct: { enabled: true, threshold: null },
  recall_threshold: null,
  recall_count: 3,
  rerank: { enabled: false, model_id: null },
};
const DEFAULT_REPLY: AppReplyConfig = {
  fallback_text: "未检索到相关知识，无法回答该问题。",
  opening: "",
  opening_questions: [],
  suggestions: false,
  allow_download: false,
};

/** 补全配置结构供表单使用；`null`（如阈值、记忆轮数）原样保留，表示用代码默认值。 */
export function withConfigDefaults(config: KbQaAppConfig): AppConfigForm {
  return {
    schema_version: config.schema_version ?? 1,
    app_type: "kb_qa",
    model: { ...DEFAULT_MODEL, ...(config.model ?? {}) },
    system_prompt: config.system_prompt ?? "",
    intent: { ...DEFAULT_INTENT, ...(config.intent ?? {}) },
    rewrite: { ...DEFAULT_REWRITE, ...(config.rewrite ?? {}) },
    memory_turns: config.memory_turns ?? null,
    knowledge: {
      kbs: config.knowledge?.kbs ?? [],
      doc: {
        ...DEFAULT_DOC,
        ...(config.knowledge?.doc ?? {}),
        rerank: { ...DEFAULT_DOC.rerank, ...(config.knowledge?.doc?.rerank ?? {}) },
      },
      qa: {
        ...DEFAULT_QA,
        ...(config.knowledge?.qa ?? {}),
        direct: { ...DEFAULT_QA.direct, ...(config.knowledge?.qa?.direct ?? {}) },
        rerank: { ...DEFAULT_QA.rerank, ...(config.knowledge?.qa?.rerank ?? {}) },
      },
    },
    reply: { ...DEFAULT_REPLY, ...(config.reply ?? {}) },
  };
}

/** 提交草稿的请求体：配置 + 当前 revision（乐观锁）。 */
export function buildDraftPayload(config: AppConfigForm, draftRevision: number): DraftIn {
  return { config: config as KbQaAppConfig, draft_revision: draftRevision };
}

/** 调试预览请求体：问题、当前草稿 revision、由前端带上的历史（ChatPanel 已截到 20 轮）。 */
export function buildDebugChatBody(
  question: string,
  history: DebugMessageIn[],
  draftRevision: number,
): DebugChatIn {
  return { question, draft_revision: draftRevision, history };
}

/** 从发布失败的错误里取问题清单（不是 `PUBLISH_CHECK_FAILED` 时返回空数组）。 */
export function publishProblems(error: unknown): PublishProblem[] {
  if (!(error instanceof ApiError) || error.code !== "PUBLISH_CHECK_FAILED") {
    return [];
  }
  const detail = error.detail as { problems?: PublishProblem[] } | null | undefined;
  return Array.isArray(detail?.problems) ? detail.problems : [];
}

/** 已启用的回答模型（模型与提示词里的回答模型选择器）。 */
export function useChatModels() {
  return useQuery({
    queryKey: ["models", "chat", "enabled"],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/models", {
          params: { query: { type: "chat", status: "enabled", page_size: 100 } },
        }),
      ),
    staleTime: 60_000,
  });
}

/** 已启用的重排模型（文档库检索的重排模型选择器）。 */
export function useRerankModels() {
  return useQuery({
    queryKey: ["models", "rerank", "enabled"],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/models", {
          params: { query: { type: "rerank", status: "enabled", page_size: 100 } },
        }),
      ),
    staleTime: 60_000,
  });
}

/** 草稿；id 为 null 时不查询。 */
export function useAppDraft(id: number | null) {
  return useQuery({
    queryKey: ["app-draft", id],
    enabled: id !== null,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/apps/{id}/draft", { params: { path: { id: id as number } } }),
      ),
  });
}

function useInvalidateDraft() {
  const queryClient = useQueryClient();
  return (id: number) => {
    void queryClient.invalidateQueries({ queryKey: ["app-draft", id] });
    void queryClient.invalidateQueries({ queryKey: ["app", id] });
    void queryClient.invalidateQueries({ queryKey: ["app-versions", id] });
    // 草稿里的引用知识库可能变了，候选列表的 added / selectable 要一起刷新（否则移除后无法再加回）。
    void queryClient.invalidateQueries({ queryKey: ["app-kb-candidates", id] });
  };
}

export function useSaveAppDraft() {
  const invalidate = useInvalidateDraft();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, body }: { id: number; body: DraftIn }) =>
      unwrap(
        await client.PUT("/api/v1/apps/{id}/draft", { params: { path: { id } }, body }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: (data, variables) => {
      queryClient.setQueryData(["app-draft", variables.id], data);
      invalidate(variables.id);
    },
  });
}

export function usePublishApp() {
  const invalidate = useInvalidateDraft();
  return useMutation({
    mutationFn: async ({ id, body }: { id: number; body: PublishIn }) =>
      unwrap(
        await client.POST("/api/v1/apps/{id}:publish", { params: { path: { id } }, body }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: (_data, variables) => invalidate(variables.id),
  });
}

/** 发布历史；id 为 null 时不查询。 */
export function useAppVersions(id: number | null, page = 1, pageSize = 20) {
  return useQuery({
    queryKey: ["app-versions", id, page, pageSize],
    enabled: id !== null,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/apps/{id}/versions", {
          params: { path: { id: id as number }, query: { page, page_size: pageSize } },
        }),
      ),
    placeholderData: (previous) => previous,
  });
}

export function useRestoreAppVersion() {
  const invalidate = useInvalidateDraft();
  return useMutation({
    mutationFn: async ({ id, version }: { id: number; version: number }) =>
      unwrap(
        await client.POST("/api/v1/apps/{id}/versions/{v}:restore", {
          params: { path: { id, v: version } },
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: (_data, variables) => invalidate(variables.id),
  });
}

/** 可添加的知识库；id 为 null 时不查询。 */
export function useKbCandidates(id: number | null, enabled = true) {
  return useQuery({
    queryKey: ["app-kb-candidates", id],
    enabled: id !== null && enabled,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/apps/{id}/kb-candidates", {
          params: { path: { id: id as number } },
        }),
      ),
  });
}

/** 系统拼接的固定部分与默认模板；enabled 为 false 时不查询。 */
export function usePromptPreview(id: number | null, enabled = true) {
  return useQuery({
    queryKey: ["app-prompt-preview", id],
    enabled: id !== null && enabled,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/apps/{id}/prompt-preview", {
          params: { path: { id: id as number } },
        }),
      ),
  });
}

/** 配置里一条知识库引用的展示文案（原型 #v-app 的“文档库 员工制度库 …”行）。 */
export function kbRefSummary(ref: AppKbRef): string {
  const retrieval = ref.retrieval;
  if (!retrieval) {
    return "知识库默认配置";
  }
  const mode =
    retrieval.mode === "vector" ? "向量" : retrieval.mode === "fulltext" ? "全文" : "混合";
  const weights =
    retrieval.mode === "hybrid"
      ? ` ${retrieval.vector_weight} / ${retrieval.fulltext_weight}`
      : "";
  return `${mode}${weights} · 已覆盖默认配置`;
}

/** 版本“查看配置”弹窗里的关键配置摘要。 */
export function configSummaryRows(config: KbQaAppConfig): { label: string; value: string }[] {
  const form = withConfigDefaults(config);
  const model = form.model.chat_model_id;
  const kbs = form.knowledge.kbs.map((ref) => `#${ref.kb_id}（${kbRefSummary(ref)}）`).join("、");
  return [
    { label: "回答模型", value: model == null ? "未选择" : `#${model}` },
    { label: "温度", value: String(form.model.temperature) },
    { label: "最大输出", value: form.model.max_tokens == null ? "默认" : String(form.model.max_tokens) },
    { label: "系统提示词", value: form.system_prompt || "（空）" },
    {
      label: "意图识别",
      value: form.intent.enabled ? `开启（${form.intent.model_id ?? "同回答模型"}）` : "关闭",
    },
    {
      label: "问题改写",
      value: form.rewrite.enabled ? `开启（${form.rewrite.model_id ?? "同回答模型"}）` : "关闭",
    },
    {
      label: "记忆轮数",
      value: form.memory_turns == null ? "默认（待实测）" : String(form.memory_turns),
    },
    { label: "知识库", value: kbs || "未引用" },
    {
      label: "文档库检索",
      value: `Top K ${form.knowledge.doc.top_k} · 分数阈值 ${
        form.knowledge.doc.score_threshold == null ? "默认（待实测）" : form.knowledge.doc.score_threshold
      } · 重排${form.knowledge.doc.rerank.enabled ? "开启" : "关闭"}`,
    },
    { label: "兜底话术", value: form.reply.fallback_text },
    { label: "开场白", value: form.reply.opening || "（空）" },
    { label: "允许下载原文件", value: form.reply.allow_download ? "开启" : "关闭" },
  ];
}
