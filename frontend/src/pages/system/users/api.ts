import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { client, unwrap } from "../../../api/client";
import type { components } from "../../../api/schema";

export type Org = components["schemas"]["OrgOut"];
export type User = components["schemas"]["UserOut"];
export type UserCreate = components["schemas"]["UserCreate"];
export type UserPatch = components["schemas"]["UserPatch"];
export type RevokeSysAdminIn = components["schemas"]["RevokeSysAdminIn"];
export type Role = components["schemas"]["Role"];
export type UserStatus = components["schemas"]["UserStatus"];
/** 新建、编辑用户时可指定的角色（不含系统管理员，任命走单独的接口）。 */
export type AssignableRole = UserCreate["role"];

export interface UserListParams {
  org_id?: number;
  include_sub?: boolean;
  role?: Role;
  status?: UserStatus;
  keyword?: string;
  page?: number;
  page_size?: number;
}

/** 管理范围内的全部组织，平铺返回。 */
export function useOrgs() {
  return useQuery({
    queryKey: ["orgs"],
    queryFn: async () => unwrap(await client.GET("/api/v1/orgs")),
    staleTime: 60_000,
  });
}

export function useUsers(params: UserListParams) {
  return useQuery({
    queryKey: ["users", params],
    queryFn: async () =>
      unwrap(await client.GET("/api/v1/users", { params: { query: params } })),
    placeholderData: (previous) => previous,
  });
}

function useInvalidateUsers() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["users"] });
}

/** 新建用户的错误由表单内显示，不弹全局提示。 */
export function useCreateUser() {
  const invalidate = useInvalidateUsers();
  return useMutation({
    mutationFn: async (body: UserCreate) =>
      unwrap(await client.POST("/api/v1/users", { body })),
    meta: { skipErrorToast: true },
    onSuccess: () => {
      void invalidate();
    },
  });
}

export function useUpdateUser() {
  const invalidate = useInvalidateUsers();
  return useMutation({
    mutationFn: async ({ id, body }: { id: number; body: UserPatch }) =>
      unwrap(
        await client.PATCH("/api/v1/users/{id}", { params: { path: { id } }, body }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: () => {
      void invalidate();
    },
  });
}

export function useDisableUser() {
  const invalidate = useInvalidateUsers();
  return useMutation({
    mutationFn: async (id: number) =>
      unwrap(await client.POST("/api/v1/users/{id}:disable", { params: { path: { id } } })),
    onSuccess: () => {
      void invalidate();
    },
  });
}

export function useEnableUser() {
  const invalidate = useInvalidateUsers();
  return useMutation({
    mutationFn: async (id: number) =>
      unwrap(await client.POST("/api/v1/users/{id}:enable", { params: { path: { id } } })),
    onSuccess: () => {
      void invalidate();
    },
  });
}

export function useResetPassword() {
  return useMutation({
    mutationFn: async ({ id, newPassword }: { id: number; newPassword: string }) =>
      unwrap(
        await client.POST("/api/v1/users/{id}:reset-password", {
          params: { path: { id } },
          body: { new_password: newPassword },
        }),
      ),
    meta: { skipErrorToast: true },
  });
}

export function useUnlockUser() {
  const invalidate = useInvalidateUsers();
  return useMutation({
    mutationFn: async (id: number) =>
      unwrap(await client.POST("/api/v1/users/{id}:unlock", { params: { path: { id } } })),
    onSuccess: () => {
      void invalidate();
    },
  });
}

export function useGrantSysAdmin() {
  const invalidate = useInvalidateUsers();
  return useMutation({
    mutationFn: async (id: number) =>
      unwrap(
        await client.POST("/api/v1/users/{id}:grant-sys-admin", {
          params: { path: { id } },
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: () => {
      void invalidate();
    },
  });
}

export function useRevokeSysAdmin() {
  const invalidate = useInvalidateUsers();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, body }: { id: number; body: RevokeSysAdminIn }) =>
      unwrap(
        await client.POST("/api/v1/users/{id}:revoke-sys-admin", {
          params: { path: { id } },
          body,
        }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: () => {
      void invalidate();
      // 撤销自己时当前用户角色会变，刷新 /me
      void queryClient.invalidateQueries({ queryKey: ["me"] });
    },
  });
}
