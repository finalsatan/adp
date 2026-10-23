import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { client, unwrap } from "./client";
import type { components } from "./schema";

export type Settings = components["schemas"]["SettingsOut"];
export type SettingsPatch = components["schemas"]["SettingsPatch"];

/** 系统设置：只有系统管理员可见，后端要求 settings.manage。 */
export function useSettings() {
  return useQuery({
    queryKey: ["settings"],
    queryFn: async () => unwrap(await client.GET("/api/v1/settings")),
  });
}

/** 保存设置；422 由页面自行映射到字段，不再弹全局提示。 */
export function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: SettingsPatch) =>
      unwrap(await client.PATCH("/api/v1/settings", { body })),
    meta: { skipErrorToast: true },
    onSuccess: (data) => {
      queryClient.setQueryData(["settings"], data);
    },
  });
}
