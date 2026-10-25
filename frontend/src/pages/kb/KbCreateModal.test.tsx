import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../api/errors";
import { useMe, type Me } from "../../api/me";
import { useOrgs } from "../../api/orgs";
import { useCreateKb, useEmbeddingModels } from "./api";
import { KbCreateModal } from "./KbCreateModal";

vi.mock("../../api/me", () => ({ useMe: vi.fn() }));
vi.mock("../../api/orgs", () => ({ useOrgs: vi.fn() }));
vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    useCreateKb: vi.fn(),
    useEmbeddingModels: vi.fn(),
  };
});

const mockedUseMe = vi.mocked(useMe);
const mockedUseOrgs = vi.mocked(useOrgs);
const mockedUseCreateKb = vi.mocked(useCreateKb);
const mockedUseEmbeddingModels = vi.mocked(useEmbeddingModels);

const ME: Me = {
  id: 1,
  username: "zhangsan",
  name: "张三",
  email: null,
  phone: null,
  role: "builder",
  org: { id: 1, name: "研发中心" },
  permissions: ["console.use", "resource.create"],
  must_change_password: false,
};

beforeEach(() => {
  mockedUseMe.mockReturnValue({ data: ME } as unknown as ReturnType<typeof useMe>);
  mockedUseOrgs.mockReturnValue({ data: [] } as unknown as ReturnType<typeof useOrgs>);
  mockedUseEmbeddingModels.mockReturnValue({
    data: {
      items: [
        {
          id: 5,
          name: "text-embedding-v3",
          embedding_dim: 1024,
        },
      ],
    },
  } as unknown as ReturnType<typeof useEmbeddingModels>);
  mockedUseCreateKb.mockReturnValue({
    isPending: false,
    mutate: vi.fn(),
  } as unknown as ReturnType<typeof useCreateKb>);
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderModal(onCreated = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <KbCreateModal open onClose={vi.fn()} onCreated={onCreated} />
      </AntApp>
    </QueryClientProvider>,
  );
}

describe("KbCreateModal", () => {
  it("提交时带上类型与向量化模型", async () => {
    const mutate = vi.fn((body, options?: { onSuccess?: (kb: unknown) => void }) => {
      options?.onSuccess?.({ id: 9, name: body.name });
    });
    mockedUseCreateKb.mockReturnValue({
      isPending: false,
      mutate,
    } as unknown as ReturnType<typeof useCreateKb>);

    renderModal();
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "员工制度库" } });
    fireEvent.change(screen.getByLabelText("描述"), { target: { value: "人事制度" } });

    fireEvent.mouseDown(screen.getByLabelText("向量化模型"));
    fireEvent.click(await screen.findByText("text-embedding-v3 · 1024 维"));

    fireEvent.click(screen.getByRole("button", { name: /创\s*建/ }));

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0]![0]).toMatchObject({
      kind: "doc",
      name: "员工制度库",
      description: "人事制度",
      embedding_model_id: 5,
      visibility: "org",
    });
  });

  it("重名时在名称输入框下显示错误", async () => {
    const mutate = vi.fn((_body, options?: { onError?: (error: unknown) => void }) => {
      options?.onError?.(new ApiError(409, { code: "NAME_CONFLICT", message: "名称已存在" }));
    });
    mockedUseCreateKb.mockReturnValue({
      isPending: false,
      mutate,
    } as unknown as ReturnType<typeof useCreateKb>);

    renderModal();
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "员工制度库" } });
    fireEvent.mouseDown(screen.getByLabelText("向量化模型"));
    fireEvent.click(await screen.findByText("text-embedding-v3 · 1024 维"));
    fireEvent.click(screen.getByRole("button", { name: /创\s*建/ }));

    expect(await screen.findByText("名称已存在")).toBeInTheDocument();
  });

  it("类型与向量化模型提示创建后不可修改", () => {
    renderModal();
    expect(screen.getByText("创建后不可修改")).toBeInTheDocument();
    expect(
      screen.getByText("创建后不可修改；要更换只能新建知识库"),
    ).toBeInTheDocument();
  });

  it("默认类型为文档库、可见范围为本组织", () => {
    renderModal();
    expect(screen.getByRole("radio", { name: "文档库" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "本组织" })).toBeChecked();
  });

  it("非系统管理员不请求组织列表", () => {
    renderModal();
    expect(mockedUseOrgs).toHaveBeenCalledWith({ enabled: false });
  });

  it("系统管理员请求组织列表并显示所属组织", () => {
    mockedUseMe.mockReturnValue({
      data: { ...ME, role: "sys_admin", org: null },
    } as unknown as ReturnType<typeof useMe>);
    renderModal();
    expect(mockedUseOrgs).toHaveBeenCalledWith({ enabled: true });
    expect(screen.getByText("所属组织")).toBeInTheDocument();
  });
});
