import { useQuery } from "@tanstack/react-query";

import { client, unwrap } from "../../../api/client";
import type { components } from "../../../api/schema";

export type AuditLog = components["schemas"]["AuditOut"];
export type AuditMeta = components["schemas"]["AuditMetaOut"];
export type AuditResult = components["schemas"]["AuditResult"];

export interface AuditListParams {
  from?: string;
  to?: string;
  actor?: string;
  action?: string;
  target_type?: string;
  target?: string;
  org_id?: number;
  result?: AuditResult;
  page?: number;
  page_size?: number;
}

export function useAuditLogs(params: AuditListParams) {
  return useQuery({
    queryKey: ["audit-logs", params],
    queryFn: async () =>
      unwrap(await client.GET("/api/v1/audit-logs", { params: { query: params } })),
    placeholderData: (previous) => previous,
  });
}

/** 动作、对象类型的选项（来自后端，代码和中文名写在代码里）。 */
export function useAuditMeta() {
  return useQuery({
    queryKey: ["audit-meta"],
    queryFn: async () => unwrap(await client.GET("/api/v1/audit-logs/meta")),
    staleTime: 300_000,
  });
}
