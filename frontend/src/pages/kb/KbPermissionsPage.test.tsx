import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useUserSearch } from "../../components/resource/api";
import {
  useAddKbMember,
  useChangeKbVisibility,
  useKb,
  useKbMembers,
  useRemoveKbMember,
  type Kb,
} from "./api";
import KbPermissionsPage from "./KbPermissionsPage";

vi.mock("../../components/resource/api", () => ({ useUserSearch: vi.fn() }));
vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    useKb: vi.fn(),
    useKbMembers: vi.fn(),
    useChangeKbVisibility: vi.fn(),
    useRemoveKbMember: vi.fn(),
    useAddKbMember: vi.fn(),
  };
});

const mockedUseKb = vi.mocked(useKb);
const mockedUseKbMembers = vi.mocked(useKbMembers);
const mockedUseChangeKbVisibility = vi.mocked(useChangeKbVisibility);
const mockedUseRemoveKbMember = vi.mocked(useRemoveKbMember);
const mockedUseAddKbMember = vi.mocked(useAddKbMember);
const mockedUseUserSearch = vi.mocked(useUserSearch);

const KB: Kb = {
  id: 1,
  name: "员工制度库",
  description: null,
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
  mockedUseKbMembers.mockReturnValue({
    data: [
      {
        user: { id: 2, username: "lisi", name: "李四", org: { id: 1, name: "研发中心" } },
        added_by: { id: 1, username: "zhangsan", name: "张三" },
        added_at: "2025-09-20T09:00:00+08:00",
      },
    ],
    isLoading: false,
  } as unknown as ReturnType<typeof useKbMembers>);
  mockedUseChangeKbVisibility.mockImplementation(() => mutationStub());
  mockedUseRemoveKbMember.mockImplementation(() => mutationStub());
  mockedUseAddKbMember.mockImplementation(() => mutationStub());
  mockedUseUserSearch.mockReturnValue({ data: [] } as unknown as ReturnType<typeof useUserSearch>);
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <MemoryRouter initialEntries={["/kb/1/permissions"]}>
          <Routes>
            <Route path="/kb/:id/permissions" element={<KbPermissionsPage />} />
          </Routes>
        </MemoryRouter>
      </AntApp>
    </QueryClientProvider>,
  );
}

describe("KbPermissionsPage", () => {
  it("管理者能改可见范围、看到协作者与添加按钮", async () => {
    renderPage();

    expect(await screen.findByText("lisi 李四")).toBeInTheDocument();
    expect(screen.getByText("研发中心")).toBeInTheDocument();
    expect(screen.getByText("添加协作者")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "仅自己" })).toBeEnabled();
  });

  it("协作者不能改可见范围、增减协作者", async () => {
    mockedUseKb.mockReturnValue({
      data: { ...KB, manageable: false },
      isLoading: false,
    } as unknown as ReturnType<typeof useKb>);
    renderPage();

    expect(await screen.findByText("lisi 李四")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "仅自己" })).toBeDisabled();
    expect(screen.queryByText("添加协作者")).toBeNull();
    expect(screen.queryByText("移除")).toBeNull();
  });
});
