import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useKb, useMetadataFields, type Kb } from "../api";
import { useFolders } from "../documents/api";
import HitTestPage from "./HitTestPage";
import {
  useHitTest,
  useRerankModels,
  useRetrievalConfig,
  useUpdateRetrievalConfig,
  type HitItem,
  type HitTestOut,
} from "./api";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, useKb: vi.fn(), useMetadataFields: vi.fn() };
});

vi.mock("../documents/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../documents/api")>();
  return { ...actual, useFolders: vi.fn() };
});

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    useRetrievalConfig: vi.fn(),
    useUpdateRetrievalConfig: vi.fn(),
    useHitTest: vi.fn(),
    useRerankModels: vi.fn(),
  };
});

const mockedUseKb = vi.mocked(useKb);
const mockedMetadataFields = vi.mocked(useMetadataFields);
const mockedFolders = vi.mocked(useFolders);
const mockedRetrievalConfig = vi.mocked(useRetrievalConfig);
const mockedUpdateConfig = vi.mocked(useUpdateRetrievalConfig);
const mockedHitTest = vi.mocked(useHitTest);
const mockedRerankModels = vi.mocked(useRerankModels);

const KB = {
  id: 1,
  name: "员工制度库",
  kind: "doc",
  doc_count: 3,
  chunk_count: 100,
  embedding_model: { id: 1, name: "text-embedding-v3" },
  visibility: "org",
  owner: { id: 1, name: "张三" },
  editable: true,
  manageable: true,
} as unknown as Kb;

const DEFAULT_CONFIG = {
  mode: "hybrid",
  vector_weight: 0.7,
  fulltext_weight: 0.3,
  top_k: 20,
  score_threshold: 0.35,
  rerank: { enabled: true, model_id: 9, top_n: 5 },
  adjacent_n: 0,
} as const;

function hit(overrides: Partial<HitItem>): HitItem {
  return {
    chunk_id: 1,
    doc_id: 10,
    doc_name: "差旅管理办法.pdf",
    content: "内容",
    title_path: "第三章",
    kind: "text",
    positions: [],
    score: 0.9,
    scores: { vector: 0.9 },
    page_no: 3,
    edited: false,
    ...overrides,
  };
}

const RESULT: HitTestOut = {
  steps: {
    vector: [hit({ chunk_id: 1, content: "向量召回内容" })],
    fulltext: [hit({ chunk_id: 2, content: "全文召回内容" })],
    fusion: [hit({ chunk_id: 3, content: "融合内容" })],
    rerank: [hit({ chunk_id: 4, content: "重排内容" })],
  },
  final: [hit({ chunk_id: 4, content: "重排内容" })],
  timings: { vector_ms: 46, fulltext_ms: 12, fusion_ms: 3, rerank_ms: 210 },
};

function mutationStub(): never {
  return { mutate: vi.fn(), isPending: false } as never;
}

beforeEach(() => {
  mockedUseKb.mockReturnValue({ data: KB, isLoading: false } as unknown as ReturnType<typeof useKb>);
  mockedMetadataFields.mockReturnValue({ data: [] } as unknown as ReturnType<typeof useMetadataFields>);
  mockedFolders.mockReturnValue({ data: [] } as unknown as ReturnType<typeof useFolders>);
  mockedRetrievalConfig.mockReturnValue({
    data: DEFAULT_CONFIG,
  } as unknown as ReturnType<typeof useRetrievalConfig>);
  mockedUpdateConfig.mockImplementation(() => mutationStub());
  mockedHitTest.mockImplementation(() => mutationStub());
  mockedRerankModels.mockReturnValue({
    data: { items: [{ id: 9, name: "gte-rerank" }], total: 1 },
  } as unknown as ReturnType<typeof useRerankModels>);
});

afterEach(() => vi.clearAllMocks());

function renderPage() {
  return render(
    <AntApp>
      <MemoryRouter initialEntries={["/kb/1/hit-test"]}>
        <Routes>
          <Route path="/kb/:id/hit-test" element={<HitTestPage />} />
        </Routes>
      </MemoryRouter>
    </AntApp>,
  );
}

describe("HitTestPage", () => {
  it("表单默认值来自知识库默认检索配置", async () => {
    renderPage();

    expect(await screen.findByDisplayValue("20")).toBeInTheDocument();
    expect(screen.getByDisplayValue("0.35")).toBeInTheDocument();
    expect(screen.getByDisplayValue("0.7")).toBeInTheDocument();
    expect(screen.getByDisplayValue("0.3")).toBeInTheDocument();
    expect(screen.getByDisplayValue("5")).toBeInTheDocument();
  });

  it("测试提交问题与临时配置，步骤切换展示对应结果集", async () => {
    const mutate = vi.fn(
      (_body: unknown, options?: { onSuccess?: (data: HitTestOut) => void }) => {
        options?.onSuccess?.(RESULT);
      },
    );
    mockedHitTest.mockReturnValue({ mutate, isPending: false } as unknown as ReturnType<
      typeof useHitTest
    >);

    renderPage();
    await screen.findByDisplayValue("20");

    fireEvent.change(screen.getByPlaceholderText(/输入问题/), {
      target: { value: "去上海出差住酒店最多报多少钱？" },
    });
    fireEvent.click(screen.getByRole("button", { name: /测\s*试/ }));

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0]?.[0]).toMatchObject({
      question: "去上海出差住酒店最多报多少钱？",
      folder_id: null,
      metadata: null,
      config: expect.objectContaining({ mode: "hybrid", top_k: 20 }),
    });
    expect((mutate.mock.calls[0]?.[0] as { config: object }).config).not.toHaveProperty(
      "adjacent_n",
    );

    // 开启重排时默认显示重排步骤
    expect(await screen.findByText("重排内容")).toBeInTheDocument();
    expect(screen.getByText(/当前显示：重排后保留的 1 条/)).toBeInTheDocument();
    expect(screen.getByText(/向量 46ms · 全文 12ms · 重排 210ms/)).toBeInTheDocument();

    // 切到向量召回
    fireEvent.click(screen.getByText("向量召回 1"));
    expect(screen.getByText("向量召回内容")).toBeInTheDocument();
    expect(screen.queryByText("重排内容")).toBeNull();
  });

  it("保存为默认配置提交的配置对象正确", async () => {
    const mutate = vi.fn();
    mockedUpdateConfig.mockReturnValue({ mutate, isPending: false } as unknown as ReturnType<
      typeof useUpdateRetrievalConfig
    >);

    renderPage();
    await screen.findByDisplayValue("20");

    fireEvent.click(screen.getByRole("button", { name: /保存为默认配置/ }));

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    const variables = mutate.mock.calls[0]?.[0] as { id: number; config: object };
    expect(variables.id).toBe(1);
    expect(variables.config).toMatchObject({
      mode: "hybrid",
      vector_weight: 0.7,
      fulltext_weight: 0.3,
      top_k: 20,
      score_threshold: 0.35,
      rerank: { enabled: true, model_id: 9, top_n: 5 },
    });
    expect(variables.config).not.toHaveProperty("adjacent_n");
  });

  it("没有编辑权限时保存按钮禁用", async () => {
    mockedUseKb.mockReturnValue({
      data: { ...KB, editable: false },
      isLoading: false,
    } as unknown as ReturnType<typeof useKb>);

    renderPage();
    await screen.findByDisplayValue("20");
    expect(screen.getByRole("button", { name: /保存为默认配置/ })).toBeDisabled();
  });
});
