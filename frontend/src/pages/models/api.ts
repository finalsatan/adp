import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { client, unwrap } from "../../api/client";
import type { components } from "../../api/schema";

export type Model = components["schemas"]["ModelOut"];
export type ModelCreate = components["schemas"]["ModelCreate"];
export type ModelUpdate = components["schemas"]["ModelUpdate"];
export type ModelRef = components["schemas"]["ModelRefOut"];
export type ModelTestIn = components["schemas"]["ModelTestIn"];
export type ModelTestResult = components["schemas"]["ModelTestResult"];
export type ModelType = components["schemas"]["ModelType"];
export type ModelStatus = components["schemas"]["ModelStatus"];

export interface ModelListParams {
  type?: ModelType;
  status?: ModelStatus;
  keyword?: string;
  page?: number;
  page_size?: number;
}

export function useModels(params: ModelListParams) {
  return useQuery({
    queryKey: ["models", params],
    queryFn: async () =>
      unwrap(await client.GET("/api/v1/models", { params: { query: params } })),
    placeholderData: (previous) => previous,
  });
}

/** 引用该模型的资源清单；id 为 null 时不查询。 */
export function useModelRefs(id: number | null) {
  return useQuery({
    queryKey: ["model-refs", id],
    enabled: id !== null,
    queryFn: async () =>
      unwrap(
        await client.GET("/api/v1/models/{id}/refs", {
          params: { path: { id: id as number } },
        }),
      ),
  });
}

function useInvalidateModels() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["models"] });
}

export function useCreateModel() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: async (body: ModelCreate) =>
      unwrap(await client.POST("/api/v1/models", { body })),
    meta: { skipErrorToast: true },
    onSuccess: () => void invalidate(),
  });
}

export function useUpdateModel() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: async ({ id, body }: { id: number; body: ModelUpdate }) =>
      unwrap(
        await client.PATCH("/api/v1/models/{id}", { params: { path: { id } }, body }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: () => void invalidate(),
  });
}

export function useDisableModel() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: async (id: number) =>
      unwrap(
        await client.POST("/api/v1/models/{id}:disable", { params: { path: { id } } }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: () => void invalidate(),
  });
}

export function useEnableModel() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: async (id: number) =>
      unwrap(
        await client.POST("/api/v1/models/{id}:enable", { params: { path: { id } } }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: () => void invalidate(),
  });
}

export function useDeleteModel() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: async (id: number) =>
      unwrap(await client.DELETE("/api/v1/models/{id}", { params: { path: { id } } })),
    meta: { skipErrorToast: true },
    onSuccess: () => void invalidate(),
  });
}

/** 测试已保存的模型（列表里的“测试”）。 */
export function useTestModel() {
  const invalidate = useInvalidateModels();
  return useMutation({
    mutationFn: async (id: number) =>
      unwrap(
        await client.POST("/api/v1/models/{id}:test", { params: { path: { id } } }),
      ),
    meta: { skipErrorToast: true },
    onSuccess: () => void invalidate(),
  });
}

/** 保存前的连通测试（抽屉里的“测试连通”）。 */
export function useTestModelForm() {
  return useMutation({
    mutationFn: async (body: ModelTestIn) =>
      unwrap(await client.POST("/api/v1/models:test", { body })),
    meta: { skipErrorToast: true },
  });
}
