import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { client, unwrap } from "../../../api/client";
import type { components } from "../../../api/schema";

export type Resource = components["schemas"]["ResourceOut"];
export type ResourceType = components["schemas"]["ResourceTypeOut"];
export type ResourceStatus = components["schemas"]["ResourceStatus"];
export type Visibility = components["schemas"]["Visibility"];

export interface ResourceListParams {
  type?: string;
  org_id?: number;
  include_sub?: boolean;
  owner_id?: number;
  visibility?: Visibility;
  status?: ResourceStatus;
  keyword?: string;
  page?: number;
  page_size?: number;
}

export function useResources(params: ResourceListParams) {
  return useQuery({
    queryKey: ["resources", params],
    queryFn: async () =>
      unwrap(await client.GET("/api/v1/resources", { params: { query: params } })),
    placeholderData: (previous) => previous,
  });
}

/** 已注册的资源类型；第一期为空，下拉只有“全部类型”。 */
export function useResourceTypes() {
  return useQuery({
    queryKey: ["resource-types"],
    queryFn: async () => unwrap(await client.GET("/api/v1/resource-types")),
    staleTime: 300_000,
  });
}

function useInvalidateResources() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["resources"] });
}

export function useDisableResource() {
  const invalidate = useInvalidateResources();
  return useMutation({
    mutationFn: async (id: number) =>
      unwrap(
        await client.POST("/api/v1/resources/{id}:disable", {
          params: { path: { id } },
        }),
      ),
    onSuccess: () => {
      void invalidate();
    },
  });
}

export function useEnableResource() {
  const invalidate = useInvalidateResources();
  return useMutation({
    mutationFn: async (id: number) =>
      unwrap(
        await client.POST("/api/v1/resources/{id}:enable", {
          params: { path: { id } },
        }),
      ),
    onSuccess: () => {
      void invalidate();
    },
  });
}

export function useDeleteResource() {
  const invalidate = useInvalidateResources();
  return useMutation({
    mutationFn: async (id: number) =>
      unwrap(
        await client.DELETE("/api/v1/resources/{id}", { params: { path: { id } } }),
      ),
    onSuccess: () => {
      void invalidate();
    },
  });
}
