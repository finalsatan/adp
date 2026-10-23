import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { client, unwrap } from "../../api/client";
import { isApiError } from "../../api/errors";
import type { components } from "../../api/schema";

export type Member = components["schemas"]["MemberOut"];
export type UserSearch = components["schemas"]["UserSearchOut"];
export type Visibility = components["schemas"]["Visibility"];

/** RESOURCE_IN_USE 的 detail.refs 里的引用方。 */
export interface ResourceRef {
  id: number;
  type: string;
  name: string;
}

/** 修改可见范围；RESOURCE_IN_USE 由调用方在弹窗里展示引用方。 */
export function useUpdateResourceVisibility() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, visibility }: { id: number; visibility: Visibility }) =>
      unwrap(
        await client.PATCH("/api/v1/resources/{id}/visibility", {
          params: { path: { id } },
          body: { visibility },
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["resources"] });
    },
  });
}

export function useResourceMembers(resourceId: number, enabled: boolean) {
  return useQuery({
    queryKey: ["resource-members", resourceId],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/resources/{id}/members", {
          params: { path: { id: resourceId } },
        }),
      ),
    enabled,
  });
}

export function useAddResourceMember() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, userId }: { id: number; userId: number }) =>
      unwrap(
        await client.POST("/api/v1/resources/{id}/members", {
          params: { path: { id } },
          body: { user_id: userId },
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({
        queryKey: ["resource-members", variables.id],
      });
    },
  });
}

export function useRemoveResourceMember() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, userId }: { id: number; userId: number }) =>
      unwrap(
        await client.DELETE("/api/v1/resources/{id}/members/{user_id}", {
          params: { path: { id, user_id: userId } },
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({
        queryKey: ["resource-members", variables.id],
      });
    },
  });
}

/** 添加协作者、资源总览筛选创建人用的远程用户搜索。 */
export function useUserSearch(keyword: string) {
  return useQuery({
    queryKey: ["users", "search", keyword],
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/users:search", {
          params: { query: { q: keyword } },
        }),
      ),
    enabled: keyword.trim().length > 0,
    staleTime: 30_000,
  });
}

/** 从 RESOURCE_IN_USE 错误里取出引用方；其他错误返回空数组。 */
export function resourceInUseRefs(error: unknown): ResourceRef[] {
  if (!isApiError(error) || error.code !== "RESOURCE_IN_USE") {
    return [];
  }
  const detail = error.detail;
  if (detail && typeof detail === "object" && "refs" in detail) {
    const refs = (detail as { refs?: unknown }).refs;
    if (Array.isArray(refs)) {
      return refs.filter(isResourceRef);
    }
  }
  return [];
}

function isResourceRef(value: unknown): value is ResourceRef {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const ref = value as ResourceRef;
  return typeof ref.id === "number" && typeof ref.type === "string" && typeof ref.name === "string";
}
