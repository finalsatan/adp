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
