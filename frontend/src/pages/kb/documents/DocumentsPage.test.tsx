import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useKb, type Kb } from "../api";
import {
  useBatchDocuments,
  useDeleteDocument,
  useDocuments,
  useDocumentPolling,
  useFolders,
  useMetadataFields,
  useRetryDocument,
  useSchemes,
  useUpdateDocument,
  type DocumentOut,
} from "./api";
import DocumentsPage from "./DocumentsPage";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, useKb: vi.fn() };
});

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    useDocuments: vi.fn(),
    useDocumentPolling: vi.fn(),
    useFolders: vi.fn(),
    useMetadataFields: vi.fn(),
    useSchemes: vi.fn(),
    useBatchDocuments: vi.fn(),
    useDeleteDocument: vi.fn(),
    useRetryDocument: vi.fn(),
    useUpdateDocument: vi.fn(),
  };
});

const mockedUseKb = vi.mocked(useKb);
const mockedDocuments = vi.mocked(useDocuments);
const mockedPolling = vi.mocked(useDocumentPolling);
const mockedFolders = vi.mocked(useFolders);
const mockedMetadataFields = vi.mocked(useMetadataFields);
const mockedSchemes = vi.mocked(useSchemes);
const mockedBatch = vi.mocked(useBatchDocuments);
const mockedDelete = vi.mocked(useDeleteDocument);
const mockedRetry = vi.mocked(useRetryDocument);
const mockedUpdate = vi.mocked(useUpdateDocument);

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

function doc(overrides: Partial<DocumentOut>): DocumentOut {
  return {
    id: 1,
    kb_id: 1,
    name: "差旅管理办法.pdf",
    file_type: "pdf",
    ext: "pdf",
    size: 1,
    status: "done",
    progress: 0,
    queue_position: null,
    error_message: null,
    chunk_count: 86,
    metadata: { 部门: "财务" },
    enabled: true,
    edited: false,
    scheme: { id: 1, name: "PDF · 通用", file_type: "pdf", builtin: true },
    scheme_snapshot_name: "PDF · 通用",
    ...overrides,
  } as unknown as DocumentOut;
}

function mutationStub(): never {
  return { mutate: vi.fn(), isPending: false } as never;
}

beforeEach(() => {
  mockedUseKb.mockReturnValue({ data: KB, isLoading: false } as unknown as ReturnType<typeof useKb>);
  mockedDocuments.mockReturnValue({
    data: {
      items: [
        doc({ id: 1, edited: true }),
        doc({ id: 2, name: "报销单.jpg", status: "failed", error_message: "第 5 个分块超长", chunk_count: 0 }),
        doc({ id: 3, name: "薪酬.pdf", status: "embedding", progress: 72, chunk_count: 0 }),
      ],
      total: 3,
      status_counts: {
        queued: 1,
        parsing: 1,
        chunking: 0,
        enhancing: 0,
        embedding: 0,
        done: 2,
        failed: 5,
      },
    },
    isLoading: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useDocuments>);
  mockedFolders.mockReturnValue({ data: [] } as unknown as ReturnType<typeof useFolders>);
  mockedMetadataFields.mockReturnValue({ data: [] } as unknown as ReturnType<typeof useMetadataFields>);
  mockedSchemes.mockReturnValue({ data: { items: [], total: 0 } } as unknown as ReturnType<typeof useSchemes>);
  mockedBatch.mockImplementation(() => mutationStub());
  mockedDelete.mockImplementation(() => mutationStub());
  mockedRetry.mockImplementation(() => mutationStub());
  mockedUpdate.mockImplementation(() => mutationStub());
});

afterEach(() => vi.clearAllMocks());

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <MemoryRouter initialEntries={["/kb/1"]}>
          <Routes>
            <Route path="/kb/:id" element={<DocumentsPage />} />
          </Routes>
        </MemoryRouter>
      </AntApp>
    </QueryClientProvider>,
  );
}

describe("DocumentsPage", () => {
  it("显示文档列表的状态文案、失败原因与统计", async () => {
    renderPage();

    expect(await screen.findByText("差旅管理办法.pdf")).toBeInTheDocument();
    expect(screen.getByText("已人工修改")).toBeInTheDocument();
    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(screen.getByText("失败")).toBeInTheDocument();
    expect(screen.getByText("第 5 个分块超长")).toBeInTheDocument();
    expect(screen.getByText("向量化中 72%")).toBeInTheDocument();
    expect(screen.getByText("共 3 条 · 失败 5 · 处理中 2")).toBeInTheDocument();
  });

  it("底部失败/处理中计数用全库 status_counts，不受当前页影响", async () => {
    renderPage();
    await screen.findByText("差旅管理办法.pdf");
    // 当前页只有 1 个失败、1 个处理中，但显示的是全库的 5 / 2
    expect(screen.getByText("共 3 条 · 失败 5 · 处理中 2")).toBeInTheDocument();
  });

  it("点击未分类用 uncategorized=true 筛选", async () => {
    renderPage();
    await screen.findByText("差旅管理办法.pdf");
    fireEvent.click(screen.getByText("未分类"));
    await waitFor(() => {
      const lastParams = mockedDocuments.mock.calls.at(-1)?.[1];
      expect(lastParams).toMatchObject({ uncategorized: true });
      expect(lastParams?.folder_id).toBeUndefined();
    });
  });

  it("有编辑权限时显示上传入口", async () => {
    renderPage();
    expect(await screen.findByRole("button", { name: "上传文档" })).toBeInTheDocument();
  });

  it("处理中文档时启动轮询", async () => {
    renderPage();
    await screen.findByText("差旅管理办法.pdf");
    expect(mockedPolling).toHaveBeenCalledWith(true, expect.any(Function));
  });
});
