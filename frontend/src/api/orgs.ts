import { useQuery } from "@tanstack/react-query";

import type { components } from "./schema";
import { client, unwrap } from "./client";

export type OrgTreeOption = components["schemas"]["OrgTreeOut"];

/** 当前用户管理范围内的全部组织（平铺返回，前端自己拼树）。 */
export function useOrgs(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ["orgs"],
    queryFn: async () => unwrap(await client.GET("/api/v1/orgs")),
    staleTime: 60_000,
    enabled: options.enabled ?? true,
  });
}

/** 只读组织树（`console.use` 即可调）：只有 id、parent_id、名称，供使用范围等选择器用。 */
export function useOrgTree(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ["orgs", "tree"],
    queryFn: async () => unwrap(await client.GET("/api/v1/orgs:tree")),
    staleTime: 60_000,
    enabled: options.enabled ?? true,
  });
}
