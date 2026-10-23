import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useMe } from "../../../api/me";
import type { Me } from "../../../api/me";
import {
  useDisableUser,
  useEnableUser,
  useOrgs,
  useUnlockUser,
  useUsers,
  type Org,
  type User,
} from "./api";
import UsersPage from "./UsersPage";

vi.mock("../../../api/me", () => ({ useMe: vi.fn() }));
vi.mock("../../../api/auth", () => ({
  usePolicy: () => ({
    data: { password_min_length: 8, password_min_classes: 3, access_token_minutes: 15 },
  }),
}));
vi.mock("./api", () => ({
  useOrgs: vi.fn(),
  useUsers: vi.fn(),
  useCreateUser: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateUser: () => ({ mutate: vi.fn(), isPending: false }),
  useDisableUser: vi.fn(),
  useEnableUser: vi.fn(),
  useUnlockUser: vi.fn(),
  useResetPassword: () => ({ mutate: vi.fn(), isPending: false }),
  useGrantSysAdmin: () => ({ mutate: vi.fn(), isPending: false }),
  useRevokeSysAdmin: () => ({ mutate: vi.fn(), isPending: false }),
}));

const mockedUseMe = vi.mocked(useMe);
const mockedUseOrgs = vi.mocked(useOrgs);
const mockedUseUsers = vi.mocked(useUsers);
const mockedUseDisableUser = vi.mocked(useDisableUser);
const mockedUseEnableUser = vi.mocked(useEnableUser);
const mockedUseUnlockUser = vi.mocked(useUnlockUser);

function org(id: number, parentId: number | null, path: string, name: string, userCount: number): Org {
  return {
    id,
    parent_id: parentId,
    path,
    name,
    remark: null,
    admins: [],
    user_count: userCount,
    resource_count: 0,
    child_count: 0,
    created_at: "2025-01-01T00:00:00+08:00",
    editable: false,
  };
}

const ORGS: Org[] = [
  org(1, null, "/1/", "研发中心", 46),
  org(2, 1, "/1/2/", "平台组", 18),
  org(3, 1, "/1/3/", "算法组", 12),
];

function user(partial: Partial<User> & Pick<User, "id" | "username" | "role">): User {
  return {
    name: partial.username,
    email: null,
    phone: null,
    remark: null,
    org: { id: 1, name: "研发中心" },
    status: "active",
    locked: false,
    last_login_at: null,
    created_at: "2025-01-01T00:00:00+08:00",
    created_by: null,
    owned_resource_count: 0,
    manageable: true,
    ...partial,
  };
}

const ME_ORG_ADMIN: Me = {
  id: 1,
  username: "zhangsan",
  name: "张三",
  email: "zhangsan@corp.com",
  phone: null,
  role: "org_admin" as const,
  org: { id: 1, name: "研发中心" },
  permissions: ["user.manage"],
  must_change_password: false,
};

const USERS: User[] = [
  user({ id: 1, username: "zhangsan", role: "org_admin", name: "张三", manageable: false }),
  user({ id: 2, username: "zhouba", role: "org_admin", name: "周八", manageable: false }),
  user({ id: 3, username: "lisi", role: "builder", name: "李四" }),
  user({
    id: 5,
    username: "wangwu",
    role: "builder",
    name: "王五",
    locked: true,
    last_login_at: "2025-10-19T11:03:00+08:00",
  }),
];

function mockHooks(me: Me) {
  mockedUseMe.mockReturnValue({ data: me } as unknown as ReturnType<typeof useMe>);
  mockedUseOrgs.mockReturnValue({
    data: ORGS,
  } as unknown as ReturnType<typeof useOrgs>);
  mockedUseUsers.mockImplementation((params) => {
    const items =
      params.role === "sys_admin"
        ? [user({ id: 9, username: "root", role: "sys_admin", name: "根管理员", org: null })]
        : USERS;
    return {
      data: { items, total: items.length },
      isLoading: false,
    } as unknown as ReturnType<typeof useUsers>;
  });
  mockedUseDisableUser.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useDisableUser>);
  mockedUseEnableUser.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useEnableUser>);
  mockedUseUnlockUser.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useUnlockUser>);
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <MemoryRouter>
          <UsersPage />
        </MemoryRouter>
      </AntApp>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("UsersPage", () => {
  it("组织管理员看到身份提示和含下级的用户数", async () => {
    mockHooks(ME_ORG_ADMIN);
    renderPage();

    expect(await screen.findByText(/当前身份：研发中心的组织管理员/)).toBeInTheDocument();
    expect(screen.getByText("研发中心（含下级）共 4 人")).toBeInTheDocument();
  });

  it("本人只显示个人中心，同级组织管理员不显示操作", async () => {
    mockHooks(ME_ORG_ADMIN);
    renderPage();

    const selfRow = (await screen.findByText("zhangsan")).closest("tr");
    expect(selfRow).not.toBeNull();
    expect(within(selfRow as HTMLElement).getByText("个人中心")).toBeInTheDocument();
    expect(within(selfRow as HTMLElement).queryByText("编辑")).toBeNull();

    const peerRow = screen.getByText("zhouba").closest("tr");
    expect(within(peerRow as HTMLElement).queryByText("编辑")).toBeNull();
  });

  it("已锁定用户显示已锁定且解锁排在最前", async () => {
    mockHooks(ME_ORG_ADMIN);
    renderPage();

    const row = (await screen.findByText("wangwu")).closest("tr");
    expect(within(row as HTMLElement).getByText("已锁定")).toBeInTheDocument();
    const actions = (row as HTMLElement).querySelectorAll("a");
    expect(actions[0]).toHaveTextContent("解锁");
  });

  it("系统管理员看到系统管理员分组，并能撤销系统管理员", async () => {
    mockHooks({
      ...ME_ORG_ADMIN,
      id: 100,
      username: "admin",
      role: "sys_admin",
      org: null,
    });
    renderPage();

    const sysNode = await screen.findByText("系统管理员");
    fireEvent.click(sysNode);

    expect(await screen.findByText("root")).toBeInTheDocument();
    expect(screen.getByText("撤销系统管理员")).toBeInTheDocument();
  });
});
