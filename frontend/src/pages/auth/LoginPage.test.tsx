import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { login, useOptionalMe } from "../../api/auth";
import { ApiError } from "../../api/errors";
import LoginPage, { safeRedirect } from "./LoginPage";

vi.mock("../../api/auth", () => ({
  login: vi.fn(),
  useOptionalMe: vi.fn(),
}));

const mockedLogin = vi.mocked(login);
const mockedOptionalMe = vi.mocked(useOptionalMe);

function mockNotLoggedIn() {
  mockedOptionalMe.mockReturnValue({
    data: null,
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof useOptionalMe>);
}

function renderLogin(entry: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<div>控制台首页</div>} />
          <Route path="/system/users" element={<div>用户管理页</div>} />
          <Route path="/change-password" element={<div>改密页</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function submitLogin() {
  fireEvent.change(await screen.findByLabelText("用户名"), {
    target: { value: "admin" },
  });
  fireEvent.change(screen.getByLabelText("密码"), {
    target: { value: "secret" },
  });
  fireEvent.click(screen.getByRole("button", { name: /登\s*录/ }));
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("safeRedirect", () => {
  it("拒绝站外地址和协议相对地址", () => {
    expect(safeRedirect("https://evil.com")).toBe("/");
    expect(safeRedirect("//evil.com")).toBe("/");
    expect(safeRedirect("/\\evil.com")).toBe("/");
    expect(safeRedirect(null)).toBe("/");
    expect(safeRedirect("")).toBe("/");
  });

  it("接受站内路径", () => {
    expect(safeRedirect("/system/users?page=2")).toBe("/system/users?page=2");
  });
});

describe("LoginPage", () => {
  it("redirect=https://evil.com 登录后不跳外站", async () => {
    mockNotLoggedIn();
    mockedLogin.mockResolvedValue({ must_change_password: false });
    renderLogin("/login?redirect=https://evil.com");
    await submitLogin();
    expect(await screen.findByText("控制台首页")).toBeInTheDocument();
  });

  it("redirect 为站内路径时按原路径跳转", async () => {
    mockNotLoggedIn();
    mockedLogin.mockResolvedValue({ must_change_password: false });
    renderLogin("/login?redirect=%2Fsystem%2Fusers");
    await submitLogin();
    expect(await screen.findByText("用户管理页")).toBeInTheDocument();
  });

  it("must_change_password 为真时跳改密页", async () => {
    mockNotLoggedIn();
    mockedLogin.mockResolvedValue({ must_change_password: true });
    renderLogin("/login");
    await submitLogin();
    expect(await screen.findByText("改密页")).toBeInTheDocument();
  });

  it("登录失败显示后端 message", async () => {
    mockNotLoggedIn();
    mockedLogin.mockRejectedValue(
      new ApiError(401, {
        code: "INVALID_CREDENTIALS",
        message: "用户名或密码错误",
      }),
    );
    renderLogin("/login");
    await submitLogin();
    expect(await screen.findByText("用户名或密码错误")).toBeInTheDocument();
  });
});
