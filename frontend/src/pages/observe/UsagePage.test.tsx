import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useMe, type Me } from "../../api/me";
import { useOrgs } from "../../api/orgs";
import {
  useUsageDaily,
  useUsageMeta,
  useUsageModels,
  useUsageSummary,
  type UsageSummary,
} from "./api";
import UsagePage from "./UsagePage";

vi.mock("../../api/me", () => ({ useMe: vi.fn() }));
vi.mock("../../api/orgs", () => ({ useOrgs: vi.fn() }));
vi.mock("./api", () => ({
  useUsageMeta: vi.fn(),
  useUsageModels: vi.fn(),
  useUsageSummary: vi.fn(),
  useUsageDaily: vi.fn(),
}));

const mockedUseMe = vi.mocked(useMe);
const mockedUseOrgs = vi.mocked(useOrgs);
const mockedUseMeta = vi.mocked(useUsageMeta);
const mockedUseModels = vi.mocked(useUsageModels);
const mockedUseSummary = vi.mocked(useUsageSummary);
const mockedUseDaily = vi.mocked(useUsageDaily);

const SYS_ADMIN: Me = {
  id: 1,
  username: "admin",
  name: "管理员",
  email: null,
  phone: null,
  role: "sys_admin",
  org: null,
  permissions: ["usage.view"],
  must_change_password: false,
};

const SUMMARY: UsageSummary = {
  totals: { calls: 48216, failures: 312, input_tokens: 61200000, output_tokens: 8400000 },
  total: 2,
  items: [
    { key: "1", label: "研发中心", sub_label: null, calls: 18420, failures: 96, input_tokens: 23600000, output_tokens: 3100000 },
    { key: "2", label: "平台组", sub_label: "研发中心下级", calls: 4215, failures: 38, input_tokens: 5100000, output_tokens: 700000 },
  ],
};

function mockQuery<T>(data: T) {
  return { data, isLoading: false, error: null } as unknown as ReturnType<typeof useUsageSummary>;
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <UsagePage />
      </AntApp>
    </QueryClientProvider>,
  );
}

function lastArg<T>(mock: { mock: { calls: unknown[][] } }): T {
  const call = mock.mock.calls[mock.mock.calls.length - 1];
  return (call?.[0] ?? {}) as T;
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("用量统计页", () => {
  beforeEach(() => {
    mockedUseMe.mockReturnValue(mockQuery(SYS_ADMIN) as unknown as ReturnType<typeof useMe>);
    mockedUseOrgs.mockReturnValue(
      mockQuery([
        { id: 1, name: "研发中心", parent_id: null, path: "/1/", user_count: 46 },
        { id: 2, name: "平台组", parent_id: 1, path: "/1/2/", user_count: 18 },
      ]) as unknown as ReturnType<typeof useOrgs>,
    );
    mockedUseMeta.mockReturnValue(
      mockQuery({
        sources: [
          { code: "app", label: "应用" },
          { code: "ingest", label: "入库" },
        ],
      }) as unknown as ReturnType<typeof useUsageMeta>,
    );
    mockedUseModels.mockReturnValue(
      mockQuery({ items: [{ id: 9, name: "通义千问-Max" }], total: 1 }) as unknown as ReturnType<
        typeof useUsageModels
      >,
    );
    mockedUseSummary.mockReturnValue(
      mockQuery(SUMMARY) as unknown as ReturnType<typeof useUsageSummary>,
    );
    mockedUseDaily.mockReturnValue(
      mockQuery({ items: [] }) as unknown as ReturnType<typeof useUsageDaily>,
    );
  });

  it("四个数字卡片：调用次数原数、Token 用万、失败带占比", () => {
    renderPage();
    expect(screen.getByText("48,216")).toBeInTheDocument();
    expect(screen.getByText("6,120 万")).toBeInTheDocument();
    expect(screen.getByText("840 万")).toBeInTheDocument();
    expect(screen.getByText("312")).toBeInTheDocument();
    expect(screen.getByText("0.6%")).toBeInTheDocument();
  });

  it("系统管理员显示可查看全部记录的身份文案", () => {
    renderPage();
    expect(screen.getByText(/当前身份：系统管理员，可查看全部记录。/)).toBeInTheDocument();
  });

  it("组织管理员显示只看到本组织及下级组织的身份文案", () => {
    mockedUseMe.mockReturnValue(
      mockQuery({ ...SYS_ADMIN, role: "org_admin", org: { id: 1, name: "研发中心" } }) as unknown as ReturnType<
        typeof useMe
      >,
    );
    renderPage();
    expect(
      screen.getByText(/当前身份：组织管理员，只看到本组织及下级组织发起的调用。/),
    ).toBeInTheDocument();
  });

  it("汇总表显示下级说明和占比", () => {
    renderPage();
    expect(screen.getByText("平台组")).toBeInTheDocument();
    expect(screen.getByText("研发中心下级")).toBeInTheDocument();
    expect(screen.getByText("38.2%")).toBeInTheDocument();
  });

  it("切换到应用维度时显示统计口径提示", () => {
    renderPage();
    fireEvent.click(screen.getByText("应用"));
    expect(
      screen.getByText("按应用汇总时只统计来源为“应用”“应用调试”的调用"),
    ).toBeInTheDocument();
    expect(lastArg<{ group_by: string }>(mockedUseSummary).group_by).toBe("app");
  });

  it("改筛选项时汇总和趋势都按新条件重查", async () => {
    renderPage();
    fireEvent.mouseDown(screen.getByText("全部来源"));
    fireEvent.click(await screen.findByText("入库"));
    await waitFor(() => {
      expect(lastArg<{ source?: string }>(mockedUseDaily).source).toBe("ingest");
    });
    expect(lastArg<{ source?: string }>(mockedUseSummary).source).toBe("ingest");
  });
});
