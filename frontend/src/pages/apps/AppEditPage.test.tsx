import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../api/errors";
import { streamSSE } from "../../lib/sse";
import AppEditPage from "./AppEditPage";
import {
  useApp,
  useAppDraft,
  useChatModels,
  useKbCandidates,
  usePromptPreview,
  usePublishApp,
  useRerankModels,
  useSaveAppDraft,
  type App,
  type AppConfigForm,
  type DraftOut,
  type KbCandidateOut,
} from "./api";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    useApp: vi.fn(),
    useAppDraft: vi.fn(),
    useKbCandidates: vi.fn(),
    useChatModels: vi.fn(),
    useRerankModels: vi.fn(),
    usePromptPreview: vi.fn(),
    useSaveAppDraft: vi.fn(),
    usePublishApp: vi.fn(),
  };
});
vi.mock("../../lib/sse", () => ({ streamSSE: vi.fn() }));

const mockedUseApp = vi.mocked(useApp);
const mockedUseAppDraft = vi.mocked(useAppDraft);
const mockedUseKbCandidates = vi.mocked(useKbCandidates);
const mockedUseChatModels = vi.mocked(useChatModels);
const mockedUseRerankModels = vi.mocked(useRerankModels);
const mockedUsePromptPreview = vi.mocked(usePromptPreview);
const mockedUseSaveAppDraft = vi.mocked(useSaveAppDraft);
const mockedUsePublishApp = vi.mocked(usePublishApp);
const mockedStreamSSE = vi.mocked(streamSSE);

const APP: App = {
  id: 1,
  name: "HR 政策问答",
  description: null,
  app_type: "kb_qa",
  chat_path: "/chat/1",
  org: { id: 1, name: "研发中心" },
  owner: { id: 1, username: "zhangsan", name: "张三" },
  visibility: "org",
  status: "published",
  published_version: { id: 9, version_no: 3, published_at: "2025-10-21T10:40:00+08:00" },
  draft_has_changes: true,
  draft_updated_at: null,
  usage_scope: { scope: "platform", orgs: [], users: [] },
  created_at: "2025-10-01T00:00:00+08:00",
  updated_at: "2025-10-20T10:12:00+08:00",
  editable: true,
  manageable: true,
};

function makeConfig(kbIds: number[]): AppConfigForm {
  return {
    schema_version: 1,
    app_type: "kb_qa",
    model: { chat_model_id: 12, temperature: 0.3, max_tokens: null, thinking: false },
    system_prompt: "你是公司的人事政策助手。",
    intent: { enabled: true, model_id: null },
    rewrite: { enabled: true, model_id: null },
    memory_turns: null,
    knowledge: {
      kbs: kbIds.map((kbId) => ({ kb_id: kbId, retrieval: null, filter: null, qa_category_ids: [] })),
      doc: {
        top_k: 20,
        score_threshold: null,
        rerank: { enabled: false, model_id: null, keep: 5 },
        neighbor_chunks: 0,
      },
      qa: {
        direct: { enabled: true, threshold: null },
        recall_threshold: null,
        recall_count: 3,
        rerank: { enabled: false, model_id: null },
      },
    },
    reply: {
      fallback_text: "未检索到相关知识，无法回答该问题。",
      opening: "",
      opening_questions: [],
      suggestions: false,
      allow_download: false,
    },
  };
}

function candidate(kbId: number, embeddingModelId: number, name: string): KbCandidateOut {
  return {
    kb_id: kbId,
    kind: "doc",
    name,
    embedding_model: { id: embeddingModelId, name: embeddingModelId === 1 ? "text-embedding-v3" : "bge-m3" },
    org: { id: 1, name: "研发中心" },
    status: "enabled",
    visibility: "org",
    selectable: true,
    added: true,
    reason: null,
  };
}

function draft(config: AppConfigForm): DraftOut {
  return {
    config,
    draft_revision: 3,
    has_changes: true,
    updated_at: "2025-10-20T10:12:00+08:00",
    updated_by: { id: 1, username: "zhangsan", name: "张三" },
  };
}

interface MutationOptions {
  onSuccess?: () => void;
  onError?: (error: unknown) => void;
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <MemoryRouter initialEntries={["/apps/1"]}>
          <Routes>
            <Route path="/apps/:id" element={<AppEditPage />} />
          </Routes>
        </MemoryRouter>
      </AntApp>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockedUseApp.mockReturnValue({ data: APP, isLoading: false } as unknown as ReturnType<typeof useApp>);
  mockedUseAppDraft.mockReturnValue({ data: draft(makeConfig([101])), isLoading: false } as unknown as ReturnType<
    typeof useAppDraft
  >);
  mockedUseKbCandidates.mockReturnValue({
    data: [candidate(101, 1, "员工制度库")],
    isLoading: false,
  } as unknown as ReturnType<typeof useKbCandidates>);
  mockedUseChatModels.mockReturnValue({
    data: { items: [{ id: 12, name: "通义千问-Max" }], total: 1 },
  } as unknown as ReturnType<typeof useChatModels>);
  mockedUseRerankModels.mockReturnValue({
    data: { items: [], total: 0 },
  } as unknown as ReturnType<typeof useRerankModels>);
  mockedUsePromptPreview.mockReturnValue({
    data: { fixed_part: "固定部分", default_system_prompt: "默认模板", prompt_version: "v1" },
  } as unknown as ReturnType<typeof usePromptPreview>);
  mockedUseSaveAppDraft.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useSaveAppDraft>);
  mockedUsePublishApp.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof usePublishApp>);
  mockedStreamSSE.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.clearAllMocks();
});

// 页面表单很大，jsdom 里首次渲染较慢（antd 样式注入），放宽本文件的超时。
beforeAll(() => {
  vi.setConfig({ testTimeout: 60_000 });
});

describe("AppEditPage", () => {
  it("保存草稿提交完整配置与当前 revision，null 原样保留", async () => {
    const mutate = vi.fn();
    mockedUseSaveAppDraft.mockReturnValue({ mutate, isPending: false } as unknown as ReturnType<
      typeof useSaveAppDraft
    >);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "保存草稿" }));

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    const variables = mutate.mock.calls[0]?.[0] as { id: number; body: { draft_revision: number; config: AppConfigForm } };
    expect(variables.id).toBe(1);
    expect(variables.body.draft_revision).toBe(3);
    expect(variables.body.config.app_type).toBe("kb_qa");
    expect(variables.body.config.model.chat_model_id).toBe(12);
    expect(variables.body.config.model.max_tokens).toBeNull();
    expect(variables.body.config.memory_turns).toBeNull();
    expect(variables.body.config.knowledge.doc.score_threshold).toBeNull();
  });

  it("记忆轮数与分数阈值显示“待实测”占位", async () => {
    renderPage();
    const placeholders = await screen.findAllByPlaceholderText("待实测");
    expect(placeholders).toHaveLength(2);
  });

  it("DRAFT_CONFLICT 时提示刷新且不覆盖", async () => {
    const mutate = vi.fn((_variables: unknown, options: MutationOptions) => {
      options.onError?.(
        new ApiError(409, { code: "DRAFT_CONFLICT", message: "草稿已被他人修改，请刷新后重试" }),
      );
    });
    mockedUseSaveAppDraft.mockReturnValue({ mutate, isPending: false } as unknown as ReturnType<
      typeof useSaveAppDraft
    >);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "保存草稿" }));

    expect(await screen.findByText("草稿已被他人修改，请刷新后重试")).toBeInTheDocument();
  });

  it("调试预览请求带 draft_revision 与历史", async () => {
    renderPage();

    fireEvent.change(await screen.findByPlaceholderText("输入问题，Ctrl+Enter 发送"), {
      target: { value: "去上海出差住酒店最多报多少？" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发\s*送/ }));

    await waitFor(() => expect(mockedStreamSSE).toHaveBeenCalledTimes(1));
    expect(mockedStreamSSE.mock.calls[0]?.[0]).toBe("/api/v1/apps/1/debug/chat");
    expect(mockedStreamSSE.mock.calls[0]?.[1]).toEqual({
      question: "去上海出差住酒店最多报多少？",
      draft_revision: 3,
      history: [],
    });
  });

  it("所选文档库向量化模型不同且未开重排时提示必须开启重排", async () => {
    mockedUseAppDraft.mockReturnValue({ data: draft(makeConfig([101, 102])), isLoading: false } as unknown as ReturnType<
      typeof useAppDraft
    >);
    mockedUseKbCandidates.mockReturnValue({
      data: [candidate(101, 1, "员工制度库"), candidate(102, 2, "产品手册")],
      isLoading: false,
    } as unknown as ReturnType<typeof useKbCandidates>);
    renderPage();

    expect(
      await screen.findByText(/必须开启重排才能保存/),
    ).toBeInTheDocument();
  });
});
