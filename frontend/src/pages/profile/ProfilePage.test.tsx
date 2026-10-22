import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { usePolicy, useUpdateMe } from "../../api/auth";
import { useMe } from "../../api/me";
import ProfilePage from "./ProfilePage";

vi.mock("../../api/me", () => ({ useMe: vi.fn() }));
vi.mock("../../api/auth", () => ({
  usePolicy: vi.fn(),
  useUpdateMe: vi.fn(),
  changePassword: vi.fn(),
}));

const mockedUseMe = vi.mocked(useMe);
const mockedUsePolicy = vi.mocked(usePolicy);
const mockedUseUpdateMe = vi.mocked(useUpdateMe);

const ME = {
  id: 1,
  username: "admin",
  name: "管理员",
  email: "admin@example.com",
  phone: "13800000000",
  role: "sys_admin",
  org: { id: 1, name: "平台管理" },
  permissions: ["settings.manage"] as string[],
  must_change_password: false,
};

const POLICY = {
  password_min_length: 8,
  password_min_classes: 3,
  access_token_minutes: 15,
};

function mockData() {
  mockedUseMe.mockReturnValue({
    data: ME,
  } as unknown as ReturnType<typeof useMe>);
  mockedUsePolicy.mockReturnValue({
    data: POLICY,
  } as unknown as ReturnType<typeof usePolicy>);
  const updateMe = { mutate: vi.fn(), isPending: false };
  mockedUseUpdateMe.mockReturnValue(
    updateMe as unknown as ReturnType<typeof useUpdateMe>,
  );
  return updateMe;
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <ProfilePage />
      </AntApp>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("ProfilePage", () => {
  it("用户名、所属组织、角色只读，并给出管理员维护提示", async () => {
    mockData();
    renderPage();

    expect(await screen.findByDisplayValue("admin")).toBeDisabled();
    expect(screen.getByDisplayValue("平台管理")).toBeDisabled();
    expect(screen.getByDisplayValue("系统管理员")).toBeDisabled();
    expect(
      screen.getByText("用户名、所属组织、角色由管理员维护"),
    ).toBeInTheDocument();
  });

  it("显示其他设备登录的失效时间", async () => {
    mockData();
    renderPage();
    expect(
      await screen.findByText(/其他设备上的登录最迟 15 分钟后失效/),
    ).toBeInTheDocument();
  });

  it("保存资料调用 PATCH /api/v1/me 的入参", async () => {
    const updateMe = mockData();
    renderPage();

    fireEvent.change(await screen.findByLabelText("姓名"), {
      target: { value: "新名字" },
    });
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => {
      expect(updateMe.mutate).toHaveBeenCalledWith(
        {
          name: "新名字",
          email: "admin@example.com",
          phone: "13800000000",
        },
        expect.anything(),
      );
    });

    expect(updateMe.mutate).toHaveBeenCalledWith(
      {
        name: "新名字",
        email: "admin@example.com",
        phone: "13800000000",
      },
      expect.anything(),
    );
  });
});
