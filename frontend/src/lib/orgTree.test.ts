import { describe, expect, it } from "vitest";

import {
  buildOrgTree,
  canAppointOrgAdmin,
  descendantNames,
  toOrgSelectData,
  type Org,
} from "./orgTree";

function org(
  id: number,
  parentId: number | null,
  path: string,
  name: string,
  userCount: number,
): Org {
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

// 研发中心(1, 直属 40) ├ 平台组(2, 18) └ 算法组(3, 12) ├ 算法一组(4, 5) └ 算法二组(5, 1)
const ORGS: Org[] = [
  org(1, null, "/1/", "研发中心", 40),
  org(2, 1, "/1/2/", "平台组", 18),
  org(3, 1, "/1/3/", "算法组", 12),
  org(4, 3, "/1/3/4/", "算法一组", 5),
  org(5, 3, "/1/3/5/", "算法二组", 1),
];

describe("buildOrgTree", () => {
  it("把平铺列表拼成树", () => {
    const roots = buildOrgTree(ORGS);
    expect(roots).toHaveLength(1);
    const root = roots[0];
    expect(root?.org.name).toBe("研发中心");
    expect(root?.children.map((node) => node.org.name)).toEqual(["平台组", "算法组"]);
    const algo = root?.children[1];
    expect(algo?.children.map((node) => node.org.name)).toEqual(["算法一组", "算法二组"]);
  });

  it("累加含下级的用户数", () => {
    const root = buildOrgTree(ORGS)[0];
    expect(root?.totalUserCount).toBe(76); // 40 + 18 + 12 + 5 + 1
    expect(root?.children[0]?.totalUserCount).toBe(18);
    const algo = root?.children[1];
    expect(algo?.totalUserCount).toBe(18); // 12 + 5 + 1
    expect(algo?.children[0]?.totalUserCount).toBe(5);
  });

  it("父组织不在列表里时当作根节点", () => {
    const roots = buildOrgTree([org(9, 99, "/99/9/", "孤岛", 2)]);
    expect(roots.map((node) => node.org.name)).toEqual(["孤岛"]);
  });

  it("转成 TreeSelect 数据", () => {
    const data = toOrgSelectData(buildOrgTree(ORGS));
    expect(data).toEqual([
      {
        value: 1,
        title: "研发中心",
        children: [
          { value: 2, title: "平台组", children: [] },
          {
            value: 3,
            title: "算法组",
            children: [
              { value: 4, title: "算法一组", children: [] },
              { value: 5, title: "算法二组", children: [] },
            ],
          },
        ],
      },
    ]);
  });
});

describe("descendantNames", () => {
  it("返回全部下级组织名称，不含自己", () => {
    expect(descendantNames(ORGS, "/1/")).toEqual([
      "平台组",
      "算法组",
      "算法一组",
      "算法二组",
    ]);
    expect(descendantNames(ORGS, "/1/3/")).toEqual(["算法一组", "算法二组"]);
    expect(descendantNames(ORGS, null)).toEqual([]);
  });
});

describe("canAppointOrgAdmin", () => {
  it("组织管理员只在严格下级时为真", () => {
    const me = { role: "org_admin" as const, orgPath: "/1/" };
    expect(canAppointOrgAdmin(me, "/1/")).toBe(false); // 本组织
    expect(canAppointOrgAdmin(me, "/")).toBe(false); // 上级
    expect(canAppointOrgAdmin(me, "/2/")).toBe(false); // 同级
    expect(canAppointOrgAdmin(me, "/1/2/")).toBe(true); // 直接下级
    expect(canAppointOrgAdmin(me, "/1/3/4/")).toBe(true); // 更下级
    expect(canAppointOrgAdmin(me, null)).toBe(false);
  });

  it("系统管理员恒为真", () => {
    const me = { role: "sys_admin" as const, orgPath: null };
    expect(canAppointOrgAdmin(me, "/1/")).toBe(true);
    expect(canAppointOrgAdmin(me, null)).toBe(true);
  });

  it("其他角色或空操作人为假", () => {
    expect(canAppointOrgAdmin({ role: "builder", orgPath: "/1/" }, "/1/2/")).toBe(false);
    expect(canAppointOrgAdmin(null, "/1/2/")).toBe(false);
  });
});
