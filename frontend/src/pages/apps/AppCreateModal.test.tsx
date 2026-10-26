import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../api/errors";
import { useMe, type Me } from "../../api/me";
import { useOrgs } from "../../api/orgs";
import { useCreateApp } from "./api";
import { AppCreateModal } from "./AppCreateModal";

vi.mock("../../api/me", () => ({ useMe: vi.fn() }));
vi.mock("../../api/orgs", () => ({ useOrgs: vi.fn() }));
vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    useCreateApp: vi.fn(),
  };
});

const mockedUseMe = vi.mocked(useMe);
const mockedUseOrgs = vi.mocked(useOrgs);
const mockedUseCreateApp = vi.mocked(useCreateApp);

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
  mockedUseCreateApp.mockReturnValue({
    isPending: false,
    mutate: vi.fn(),
  } as unknown as ReturnType<typeof useCreateApp>);
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderModal(onCreated = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <AppCreateModal open onClose={vi.fn()} onCreated={onCreated} />
      </AntApp>
    </QueryClientProvider>,
  );
}

describe("AppCreateModal", () => {
  it("提交时带上类型、名称、描述与可见范围", async () => {
    const mutate = vi.fn((body, options?: { onSuccess?: (app: unknown) => void }) => {
      options?.onSuccess?.({ id: 9, name: body.name });
    });
    mockedUseCreateApp.mockReturnValue({
      isPending: false,
      mutate,
    } as unknown as ReturnType<typeof useCreateApp>);

    renderModal();
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "HR 政策问答" } });
    fireEvent.change(screen.getByLabelText("描述"), {
      target: { value: "员工制度、假期、社保相关问题" },
    });
    fireEvent.click(screen.getByRole("radio", { name: "全平台" }));
    fireEvent.click(screen.getByRole("button", { name: /创建并配置/ }));

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0]![0]).toMatchObject({
      app_type: "kb_qa",
      name: "HR 政策问答",
      description: "员工制度、假期、社保相关问题",
      visibility: "platform",
      org_id: null,
    });
  });

  it("重名时在名称输入框下显示错误", async () => {
    const mutate = vi.fn((_body, options?: { onError?: (error: unknown) => void }) => {
      options?.onError?.(new ApiError(409, { code: "NAME_CONFLICT", message: "名称已存在" }));
    });
    mockedUseCreateApp.mockReturnValue({
      isPending: false,
      mutate,
    } as unknown as ReturnType<typeof useCreateApp>);

    renderModal();
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "HR 政策问答" } });
    fireEvent.click(screen.getByRole("button", { name: /创建并配置/ }));

    expect(await screen.findByText("名称已存在")).toBeInTheDocument();
  });

  it("默认类型为知识问答、可见范围为本组织", () => {
    renderModal();
    expect(screen.getByRole("radio", { name: /知识问答/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: "本组织" })).toBeChecked();
  });

  it("工作流与智能体标“待细化”且禁用", () => {
    renderModal();
    expect(screen.getByRole("radio", { name: /工作流/ })).toBeDisabled();
    expect(screen.getByRole("radio", { name: /智能体/ })).toBeDisabled();
    expect(screen.getAllByText("待细化")).toHaveLength(2);
  });

  it("说明可见范围与使用范围的区别", () => {
    renderModal();
    expect(
      screen.getByText("控制谁能在控制台看到这个应用。谁能使用在发布前设置“使用范围”。"),
    ).toBeInTheDocument();
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
