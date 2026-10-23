import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../../api/errors";
import { useMe, type Me } from "../../../api/me";
import {
  useDeleteOrg,
  useOrgMembers,
  useOrgs,
  useUser,
  type Org,
  type OrgMember,
} from "./api";
import OrgsPage from "./OrgsPage";

vi.mock("../../../api/me", () => ({ useMe: vi.fn() }));
vi.mock("./api", () => ({
  useOrgs: vi.fn(),
  useCreateOrg: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateOrg: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteOrg: vi.fn(),
  useOrgMembers: vi.fn(),
  useUser: vi.fn(),
}));

const mockedUseMe = vi.mocked(useMe);
const mockedUseOrgs = vi.mocked(useOrgs);
const mockedUseDeleteOrg = vi.mocked(useDeleteOrg);
const mockedUseOrgMembers = vi.mocked(useOrgMembers);
const mockedUseUser = vi.mocked(useUser);

function org(partial: Partial<Org> & Pick<Org, "id" | "name" | "user_count">): Org {
  return {
    parent_id: null,
    path: `/${partial.id}/`,
    remark: null,
    admins: [],
    resource_count: 0,
    child_count: 0,
    created_at: "2025-09-01T00:00:00+08:00",
    editable: true,
    ...partial,
  };
}

const ORGS: Org[] = [
  org({ id: 1, name: "研发中心", user_count: 46, admins: [{ id: 10, name: "王磊", username: "wanglei" }], resource_count: 31, child_count: 2, editable: false }),
  org({ id: 2, parent_id: 1, path: "/1/2/", name: "平台组", user_count: 18, resource_count: 12, editable: true }),
  org({ id: 3, parent_id: 1, path: "/1/3/", name: "试点小组", user_count: 0, editable: true }),
];

const ME_ORG_ADMIN: Me = {
  id: 1,
  username: "zhangsan",
  name: "张三",
  email: null,
  phone: null,
  role: "org_admin",
  org: { id: 1, name: "研发中心" },
  permissions: ["org.manage"],
  must_change_password: false,
};

const ME_SYS_ADMIN: Me = {
  ...ME_ORG_ADMIN,
  id: 100,
  username: "admin",
  name: "管理员",
  role: "sys_admin",
  org: null,
  permissions: ["org.manage"],
};

const MEMBERS: OrgMember[] = [
  { id: 1, username: "zhangsan", name: "张三", role: "org_admin", status: "active", manageable: false },
  { id: 2, username: "zhouba", name: "周八", role: "org_admin", status: "active", manageable: true },
  { id: 3, username: "lisi", name: "李四", role: "builder", status: "disabled", manageable: false },
];

function mockHooks(me: Me, orgs: Org[] = ORGS) {
  mockedUseMe.mockReturnValue({ data: me } as unknown as ReturnType<typeof useMe>);
  mockedUseOrgs.mockReturnValue({ data: orgs, isLoading: false } as unknown as ReturnType<
    typeof useOrgs
  >);
  mockedUseDeleteOrg.mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<
    typeof useDeleteOrg
  >);
  mockedUseOrgMembers.mockReturnValue({
    data: { items: MEMBERS, total: MEMBERS.length },
    isLoading: false,
  } as unknown as ReturnType<typeof useOrgMembers>);
  mockedUseUser.mockReturnValue({ data: undefined } as unknown as ReturnType<typeof useUser>);
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <MemoryRouter>
          <OrgsPage />
        </MemoryRouter>
      </AntApp>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("OrgsPage", () => {
  it("系统管理员看到“新建顶级组织”，表格展示管理员、数量与全部操作", async () => {
    mockHooks(ME_SYS_ADMIN);
    renderPage();

    expect(await screen.findByText("新建顶级组织")).toBeInTheDocument();
    expect(screen.getByText("共 3 个组织")).toBeInTheDocument();

    const row = (await screen.findByText("平台组")).closest("tr") as HTMLElement;
    expect(within(row).getByText("编辑")).toBeInTheDocument();
    expect(within(row).getByText("添加下级")).toBeInTheDocument();
    expect(within(row).getByText("成员")).toBeInTheDocument();
    expect(within(row).getByText("删除")).toBeInTheDocument();

    const parentRow = screen.getByText("研发中心").closest("tr") as HTMLElement;
    expect(within(parentRow).getByText("wanglei")).toBeInTheDocument();
  });

  it("组织管理员的本组织行没有编辑和删除，下级行有", async () => {
    mockHooks(ME_ORG_ADMIN);
    renderPage();

    expect(await screen.findByText("新建下级组织")).toBeInTheDocument();

    const ownRow = (await screen.findByText("研发中心")).closest("tr") as HTMLElement;
    expect(within(ownRow).queryByText("编辑")).toBeNull();
    expect(within(ownRow).queryByText("删除")).toBeNull();
    expect(within(ownRow).getByText("添加下级")).toBeInTheDocument();
    expect(within(ownRow).getByText("成员")).toBeInTheDocument();

    const childRow = screen.getByText("平台组").closest("tr") as HTMLElement;
    expect(within(childRow).getByText("编辑")).toBeInTheDocument();
    expect(within(childRow).getByText("删除")).toBeInTheDocument();
  });

  it("没有用户的组织不显示“成员”", async () => {
    mockHooks(ME_SYS_ADMIN);
    renderPage();

    const row = (await screen.findByText("试点小组")).closest("tr") as HTMLElement;
    expect(within(row).queryByText("成员")).toBeNull();
  });

  it("成员抽屉里本人显示个人中心，可管理的显示编辑", async () => {
    mockHooks(ME_ORG_ADMIN);
    renderPage();

    const ownRow = (await screen.findByText("研发中心")).closest("tr") as HTMLElement;
    fireEvent.click(within(ownRow).getByText("成员"));

    expect(await screen.findByText("成员 · 研发中心")).toBeInTheDocument();
    const selfRow = screen.getByText("zhangsan").closest("tr") as HTMLElement;
    expect(within(selfRow).getByText("个人中心")).toBeInTheDocument();

    const peerRow = screen.getByText("zhouba").closest("tr") as HTMLElement;
    expect(within(peerRow).getByText("编辑")).toBeInTheDocument();

    const otherRow = screen.getByText("lisi").closest("tr") as HTMLElement;
    expect(within(otherRow).queryByText("编辑")).toBeNull();
    expect(within(otherRow).getByText("已停用")).toBeInTheDocument();
  });

  it("删除非空组织时提示下级组织、用户和资源的数量", async () => {
    mockHooks(ME_SYS_ADMIN);
    const error = new ApiError(409, {
      code: "ORG_NOT_EMPTY",
      message: "组织不为空",
      detail: { children: 1, users: 2, resources: 3 },
    });
    const mutate = vi.fn(
      (_id: number, options?: { onError?: (error: unknown) => void }) => {
        options?.onError?.(error);
      },
    );
    mockedUseDeleteOrg.mockReturnValue({ mutate, isPending: false } as unknown as ReturnType<
      typeof useDeleteOrg
    >);
    renderPage();

    const row = (await screen.findByText("平台组")).closest("tr") as HTMLElement;
    fireEvent.click(within(row).getByText("删除"));

    const confirm = await screen.findByRole("tooltip");
    fireEvent.click(within(confirm).getByRole("button", { name: /删\s*除/ }));

    await waitFor(() => {
      expect(screen.getByText(/还有 1 个下级组织、2 个用户、3 个资源/)).toBeInTheDocument();
    });
  });
});
