import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { client, unwrap } from "./client";
import { ApiError, readErrorBody } from "./errors";
import type { Me } from "./me";
import type { components } from "./schema";

export type AuthPolicy = components["schemas"]["AuthPolicyOut"];
export type MePatch = components["schemas"]["MePatch"];

/** 密码与令牌策略：改密页的规则提示和个人中心的失效时间用。 */
export function usePolicy() {
  return useQuery({
    queryKey: ["auth", "policy"],
    queryFn: async () => unwrap(await client.GET("/api/v1/auth/policy")),
    staleTime: 5 * 60_000,
  });
}

/**
 * 登录页判断是否已登录。直接 fetch，401 时返回 null，
 * 不经过 fetchWithRefresh，避免在登录页触发刷新失败后的跳转覆盖 redirect 参数。
 */
export function useOptionalMe() {
  return useQuery({
    queryKey: ["me", "optional"],
    queryFn: async (): Promise<Me | null> => {
      const response = await fetch("/api/v1/me", {
        headers: { accept: "application/json" },
      });
      if (response.status === 401) {
        return null;
      }
      if (!response.ok) {
        throw ApiError.from(response.status, await readErrorBody(response));
      }
      return (await response.json()) as Me;
    },
    retry: false,
    staleTime: 30_000,
  });
}

export async function login(username: string, password: string) {
  return unwrap(
    await client.POST("/api/v1/auth/login", { body: { username, password } }),
  );
}

export async function changePassword(body: {
  old_password?: string | null;
  new_password: string;
}) {
  return unwrap(await client.POST("/api/v1/auth/change-password", { body }));
}

export async function logout() {
  await client.POST("/api/v1/auth/logout");
}

/** 修改本人资料，成功后同步更新 ["me"] 缓存，顶栏姓名随之刷新。 */
export function useUpdateMe() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: MePatch) =>
      unwrap(await client.PATCH("/api/v1/me", { body })),
    onSuccess: (data) => {
      queryClient.setQueryData(["me"], data);
    },
  });
}
