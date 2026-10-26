import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { client } from "../../api/client";
import { useSaveAppDraft, type DraftOut } from "./api";

function createWrapper(queryClient: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

const DRAFT: DraftOut = {
  config: {} as DraftOut["config"],
  draft_revision: 4,
  has_changes: false,
  updated_at: "2025-10-20T10:12:00+08:00",
  updated_by: { id: 1, username: "zhangsan", name: "张三" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useSaveAppDraft", () => {
  it("保存草稿后刷新草稿、应用、发布历史与候选知识库", async () => {
    vi.spyOn(client, "PUT").mockResolvedValue({
      data: DRAFT,
      response: new Response(JSON.stringify(DRAFT), { status: 200 }),
    } as never);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useSaveAppDraft(), {
      wrapper: createWrapper(queryClient),
    });

    act(() => {
      result.current.mutate({ id: 1, body: { config: {} as never, draft_revision: 3 } });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const keys = invalidate.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(["app-draft", 1]));
    expect(keys).toContain(JSON.stringify(["app", 1]));
    expect(keys).toContain(JSON.stringify(["app-versions", 1]));
    expect(keys).toContain(JSON.stringify(["app-kb-candidates", 1]));
  });
});
