import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { client, unwrap } from "../../api/client";
import type { components } from "../../api/schema";

export type UsageSummary = components["schemas"]["UsageSummaryOut"];
export type UsageItem = components["schemas"]["UsageItem"];
export type UsageDailyItem = components["schemas"]["UsageDailyItem"];
export type UsageGroupBy = components["schemas"]["UsageGroupBy"];
export type UsageSortBy = components["schemas"]["UsageSortBy"];
export type UsageSource = components["schemas"]["UsageSource"];
export type UsageSourceOption = components["schemas"]["UsageSourceOption"];
export type UsageMeta = components["schemas"]["UsageMetaOut"];
export type ModelOut = components["schemas"]["ModelOut"];

/** 汇总与趋势共用的筛选条件。 */
export interface UsageFilters {
  from: string;
  to: string;
  model_id?: number;
  org_id?: number;
  app_id?: number;
  source?: UsageSource;
}

export interface UsageSummaryParams extends UsageFilters {
  group_by: UsageGroupBy;
  sort_by: UsageSortBy;
  order: "asc" | "desc";
  page: number;
  page_size: number;
}

/** 浏览器时区，按天趋势按它分日（技术方案 9.7）。 */
export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

/** 来源代码和中文名。 */
export function useUsageMeta() {
  return useQuery({
    queryKey: ["usage", "meta"],
    queryFn: async () => unwrap(await client.GET("/api/v1/usage/meta")),
    staleTime: 60_000,
  });
}

/** 汇总（含合计），筛选项或分组/排序/分页变化时重查。 */
export function useUsageSummary(params: UsageSummaryParams) {
  return useQuery({
    queryKey: ["usage", "summary", params],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/usage/summary", {
          params: { query: params },
        }),
      ),
    placeholderData: keepPreviousData,
  });
}

/** 按天趋势，同样随筛选项变化重查。 */
export function useUsageDaily(params: UsageFilters) {
  const tz = browserTimeZone();
  return useQuery({
    queryKey: ["usage", "daily", params, tz],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/usage/daily", {
          params: { query: { ...params, tz } },
        }),
      ),
    placeholderData: keepPreviousData,
  });
}

/** 模型筛选下拉用：全部模型，含已停用的。 */
export function useUsageModels() {
  return useQuery({
    queryKey: ["usage", "models"],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/models", {
          params: { query: { page_size: 100 } },
        }),
      ),
    staleTime: 60_000,
  });
}
