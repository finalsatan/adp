import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import MyAppsPage from "./MyAppsPage";
import { useMyApps, type MyApp } from "../chat/api";

vi.mock("../chat/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../chat/api")>();
  return { ...actual, useMyApps: vi.fn() };
});

const mockedUseMyApps = vi.mocked(useMyApps);

const APP: MyApp = {
  id: 1,
  name: "HR 政策问答",
  description: "员工制度、假期、社保相关问题",
  app_type: "kb_qa",
  org: { id: 1, name: "研发中心" },
  chat_path: "/chat/1",
};

function renderPage() {
  return render(
    <MemoryRouter>
      <MyAppsPage />
    </MemoryRouter>,
  );
}

afterEach(() => vi.clearAllMocks());

describe("MyAppsPage", () => {
  it("显示应用卡片并链接到对话页", () => {
    mockedUseMyApps.mockReturnValue({
      data: [APP],
      isLoading: false,
    } as unknown as ReturnType<typeof useMyApps>);

    renderPage();

    expect(screen.getByText("HR 政策问答")).toBeInTheDocument();
    expect(screen.getByText("员工制度、假期、社保相关问题")).toBeInTheDocument();
    expect(screen.getByText("研发中心 · 知识问答")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "开始提问" })).toHaveAttribute("href", "/chat/1");
  });

  it("没有可用应用时显示空状态", () => {
    mockedUseMyApps.mockReturnValue({
      data: [],
      isLoading: false,
    } as unknown as ReturnType<typeof useMyApps>);

    renderPage();

    expect(screen.getByText("暂无可用的应用")).toBeInTheDocument();
  });
});
