import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";

import { client, unwrap } from "../../api/client";
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
