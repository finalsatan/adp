import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { changePassword, usePolicy } from "../../api/auth";
import { ApiError } from "../../api/errors";
import { useMe } from "../../api/me";
import ChangePasswordPage from "./ChangePasswordPage";

vi.mock("../../api/me", () => ({ useMe: vi.fn() }));
vi.mock("../../api/auth", () => ({
  usePolicy: vi.fn(),
  changePassword: vi.fn(),
  logout: vi.fn(),
}));

const mockedUseMe = vi.mocked(useMe);
const mockedUsePolicy = vi.mocked(usePolicy);
const mockedChangePassword = vi.mocked(changePassword);

const ME = {
  id: 1,
  username: "admin",
  name: "管理员",
  email: null,
  phone: null,
  role: "sys_admin",
  org: null,
  permissions: [] as string[],
  must_change_password: true,
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
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <MemoryRouter initialEntries={["/change-password"]}>
          <Routes>
            <Route path="/change-password" element={<ChangePasswordPage />} />
            <Route path="/" element={<div>控制台首页</div>} />
          </Routes>
        </MemoryRouter>
      </AntApp>
    </QueryClientProvider>,
  );
}

async function fill(password: string, confirm: string) {
  fireEvent.change(await screen.findByLabelText("新密码"), {
    target: { value: password },
  });
  fireEvent.change(screen.getByLabelText("确认新密码"), {
    target: { value: confirm },
  });
  fireEvent.click(screen.getByRole("button", { name: "确认并进入平台" }));
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("ChangePasswordPage", () => {
  it("按策略显示规则清单", async () => {
    mockData();
    renderPage();
    expect(await screen.findByText("不少于 8 位")).toBeInTheDocument();
    expect(
      screen.getByText("包含大小写、数字、符号中至少 3 类"),
    ).toBeInTheDocument();
    expect(screen.getByText("不与用户名相同")).toBeInTheDocument();
    expect(screen.getByText("不是常见弱密码")).toBeInTheDocument();
  });

  it("两次输入不一致时不提交", async () => {
    mockData();
    renderPage();
    await fill("Abcdefg1!", "Abcdefg2!");
    expect(await screen.findByText("两次输入的密码不一致")).toBeInTheDocument();
    expect(mockedChangePassword).not.toHaveBeenCalled();
  });

  it("成功后跳控制台首页", async () => {
    mockData();
    mockedChangePassword.mockResolvedValue({ must_change_password: false });
    renderPage();
    await fill("Abcdefg1!", "Abcdefg1!");
    expect(await screen.findByText("控制台首页")).toBeInTheDocument();
  });

  it("WEAK_PASSWORD 时把对应规则标红", async () => {
    mockData();
    mockedChangePassword.mockRejectedValue(
      new ApiError(422, {
        code: "WEAK_PASSWORD",
        message: "密码不符合要求",
        detail: { reasons: ["COMMON_PASSWORD"] },
      }),
    );
    renderPage();
    await fill("Abcdefg1!", "Abcdefg1!");
    expect(await screen.findByText("不是常见弱密码")).toHaveClass(
      "ant-typography-danger",
    );
  });
});
