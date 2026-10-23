import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { client, unwrap } from "../../../api/client";
import type { components } from "../../../api/schema";

export type Org = components["schemas"]["OrgOut"];
export type OrgCreate = components["schemas"]["OrgCreate"];
export type OrgPatch = components["schemas"]["OrgPatch"];
export type OrgMember = components["schemas"]["OrgMemberOut"];
export type User = components["schemas"]["UserOut"];

export interface OrgMemberListParams {
  page?: number;
  page_size?: number;
}

/** 管理范围内的全部组织，平铺返回，前端自己拼树。 */
export function useOrgs() {
  return useQuery({
    queryKey: ["orgs"],
    queryFn: async () => unwrap(await client.GET("/api/v1/orgs")),
    staleTime: 60_000,
  });
}

function useInvalidateOrgs() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["orgs"] });
    void queryClient.invalidateQueries({ queryKey: ["org-members"] });
  };
}

/** 新建组织的错误由抽屉内显示，不弹全局提示。 */
export function useCreateOrg() {
  const invalidate = useInvalidateOrgs();
  return useMutation({
    mutationFn: async (body: OrgCreate) =>
      unwrap(await client.POST("/api/v1/orgs", { body })),
    meta: { skipErrorToast: true },
    onSuccess: () => invalidate(),
  });
}

export function useUpdateOrg() {
  const invalidate = useInvalidateOrgs();
  return useMutation({
    mutationFn: async ({ id, body }: { id: number; body: OrgPatch }) =>
      unwrap(
        await client.PATCH("/api/v1/orgs/{id}", { params: { path: { id } }, body }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: () => invalidate(),
  });
}

export function useDeleteOrg() {
  const invalidate = useInvalidateOrgs();
  return useMutation({
    mutationFn: async (id: number) =>
      unwrap(await client.DELETE("/api/v1/orgs/{id}", { params: { path: { id } } })),
    meta: { skipErrorToast: true },
    onSuccess: () => invalidate(),
  });
}

export function useOrgMembers(orgId: number | null, params: OrgMemberListParams) {
  return useQuery({
    queryKey: ["org-members", orgId, params],
    enabled: orgId !== null,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/orgs/{id}/members", {
          params: { path: { id: orgId as number }, query: params },
        }),
      ),
    placeholderData: (previous) => previous,
  });
}

/** 取单个用户的完整信息（成员抽屉打开编辑抽屉前用，成员接口没有这些字段）。 */
export function useUser(id: number | null) {
  return useQuery({
    queryKey: ["user", id],
    enabled: id !== null,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/users/{id}", { params: { path: { id: id as number } } }),
      ),
  });
}
