import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  useCreateMetadataField,
  useDefaultSchemes,
  useDeleteKb,
  useDeleteMetadataField,
  useDisableKb,
  useKb,
  useKbRefs,
  useMetadataFields,
  useSchemes,
  useUpdateDefaultSchemes,
  useUpdateKb,
  useUpdateMetadataField,
  type Kb,
} from "./api";
import KbSettingsPage from "./KbSettingsPage";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    useKb: vi.fn(),
    useUpdateKb: vi.fn(),
    useDefaultSchemes: vi.fn(),
    useSchemes: vi.fn(),
    useUpdateDefaultSchemes: vi.fn(),
    useMetadataFields: vi.fn(),
    useCreateMetadataField: vi.fn(),
    useUpdateMetadataField: vi.fn(),
    useDeleteMetadataField: vi.fn(),
    useKbRefs: vi.fn(),
    useDeleteKb: vi.fn(),
    useDisableKb: vi.fn(),
  };
});

const mockedUseKb = vi.mocked(useKb);
const mockedUseUpdateKb = vi.mocked(useUpdateKb);
const mockedUseDefaultSchemes = vi.mocked(useDefaultSchemes);
const mockedUseSchemes = vi.mocked(useSchemes);
const mockedUseUpdateDefaultSchemes = vi.mocked(useUpdateDefaultSchemes);
const mockedUseMetadataFields = vi.mocked(useMetadataFields);
const mockedUseCreateMetadataField = vi.mocked(useCreateMetadataField);
const mockedUseUpdateMetadataField = vi.mocked(useUpdateMetadataField);
const mockedUseDeleteMetadataField = vi.mocked(useDeleteMetadataField);
const mockedUseKbRefs = vi.mocked(useKbRefs);
const mockedUseDeleteKb = vi.mocked(useDeleteKb);
const mockedUseDisableKb = vi.mocked(useDisableKb);

const KB: Kb = {
  id: 1,
  name: "员工制度库",
  description: "人事、行政、财务制度",
  kind: "doc",
  org: { id: 1, name: "研发中心" },
  owner: { id: 1, username: "zhangsan", name: "张三" },
  visibility: "org",
  status: "enabled",
  embedding_model: {
    id: 5,
    name: "text-embedding-v3",
    embedding_dim: 1024,
    embedding_max_input: 8192,
    status: "enabled",
  },
  doc_count: 128,
  chunk_count: 4316,
  created_at: "2025-10-01T00:00:00+08:00",
  updated_at: "2025-10-20T10:12:00+08:00",
  editable: true,
  manageable: true,
};

function mutationStub<T>(): T {
  return { mutate: vi.fn(), reset: vi.fn(), isPending: false } as unknown as T;
}

beforeEach(() => {
  mockedUseKb.mockReturnValue({ data: KB, isLoading: false } as unknown as ReturnType<typeof useKb>);
  mockedUseUpdateKb.mockImplementation(() => mutationStub());
  mockedUseDefaultSchemes.mockReturnValue({
    data: {
      items: [
        {
          file_type: "pdf",
          scheme_id: 1,
          scheme_name: "PDF · 通用",
          fallback_scheme_id: 1,
          fallback_scheme_name: "PDF · 通用",
        },
        {
          file_type: "word",
          scheme_id: null,
          scheme_name: null,
          fallback_scheme_id: 2,
          fallback_scheme_name: "Word · 通用",
        },
      ],
    },
  } as unknown as ReturnType<typeof useDefaultSchemes>);
  mockedUseSchemes.mockReturnValue({
    data: { items: [{ id: 1, name: "PDF · 通用", file_type: "pdf" }], total: 1 },
  } as unknown as ReturnType<typeof useSchemes>);
  mockedUseUpdateDefaultSchemes.mockImplementation(() => mutationStub());
  mockedUseMetadataFields.mockReturnValue({ data: [] } as unknown as ReturnType<typeof useMetadataFields>);
  mockedUseCreateMetadataField.mockImplementation(() => mutationStub());
  mockedUseUpdateMetadataField.mockImplementation(() => mutationStub());
  mockedUseDeleteMetadataField.mockImplementation(() => mutationStub());
  mockedUseKbRefs.mockReturnValue({ data: [] } as unknown as ReturnType<typeof useKbRefs>);
  mockedUseDeleteKb.mockImplementation(() => mutationStub());
  mockedUseDisableKb.mockImplementation(() => mutationStub());
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <MemoryRouter initialEntries={["/kb/1/settings"]}>
          <Routes>
            <Route path="/kb/:id/settings" element={<KbSettingsPage />} />
          </Routes>
        </MemoryRouter>
      </AntApp>
    </QueryClientProvider>,
  );
}

describe("KbSettingsPage", () => {
  it("类型与向量化模型只读，并提示创建后不可修改", async () => {
    renderPage();

    expect(await screen.findByText("员工制度库")).toBeInTheDocument();
    expect(screen.getAllByText("文档库").length).toBeGreaterThan(0);
    expect(screen.getByText("text-embedding-v3")).toBeInTheDocument();
    expect(screen.getByText("创建后不可修改")).toBeInTheDocument();
    expect(screen.getByText("创建后不可修改；要更换请新建知识库")).toBeInTheDocument();
  });

  it("默认入库方案未设置时显示“平台默认”回退", async () => {
    renderPage();

    expect(await screen.findByText("平台默认：Word · 通用")).toBeInTheDocument();
    expect(screen.getByText("PDF · 通用")).toBeInTheDocument();
  });

  it("问答库设置页只显示基本信息与停用删除", async () => {
    mockedUseKb.mockReturnValue({
      data: { ...KB, kind: "qa", doc_count: 0, chunk_count: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof useKb>);
    renderPage();

    expect(await screen.findByText("员工制度库")).toBeInTheDocument();
    expect(screen.getAllByText("问答库").length).toBeGreaterThan(0);
    expect(screen.queryByText("默认入库方案")).toBeNull();
    expect(screen.queryByText("元数据字段")).toBeNull();
    expect(screen.getByText("停用与删除")).toBeInTheDocument();
  });
});
