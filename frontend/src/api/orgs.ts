import { useQuery } from "@tanstack/react-query";

import { client, unwrap } from "./client";

/** 当前用户管理范围内的全部组织（平铺返回，前端自己拼树）。 */
export function useOrgs(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ["orgs"],
    queryFn: async () => unwrap(await client.GET("/api/v1/orgs")),
    staleTime: 60_000,
    enabled: options.enabled ?? true,
  });
}
