import { describe, expect, it } from "vitest";

import type { Me } from "../api/me";
import { buildMenu } from "./menu";

function me(permissions: string[]): Me {
  return { permissions } as unknown as Me;
}

const SYS_ADMIN = me([
  "settings.manage",
  "model.manage",
  "org.manage",
  "user.manage",
  "audit.view",
  "overview.view",
  "usage.view",
  "resource.create",
  "console.use",
]);

const ORG_ADMIN = me([
  "org.manage",
  "user.manage",
  "audit.view",
  "overview.view",
  "usage.view",
  "resource.create",
  "console.use",
]);

const BUILDER = me(["resource.create", "console.use"]);

const END_USER = me([]);

function labels(menu: ReturnType<typeof buildMenu>): string[] {
  return menu.flatMap((group) => group.items.map((item) => item.label));
}

describe("buildMenu", () => {
  it("系统管理员看到模型服务和系统管理", () => {
    const menu = buildMenu(SYS_ADMIN);
    expect(menu.map((group) => group.label)).toEqual(["模型服务", "系统管理"]);
    expect(labels(menu)).toEqual([
      "模型管理",
      "模型体验",
      "用户管理",
      "组织管理",
      "资源总览",
      "审计日志",
      "系统设置",
    ]);
  });

  it("组织管理员看不到系统设置", () => {
    const menu = buildMenu(ORG_ADMIN);
    expect(menu.map((group) => group.label)).toEqual(["模型服务", "系统管理"]);
    expect(labels(menu)).toEqual([
      "模型管理",
      "模型体验",
      "用户管理",
      "组织管理",
      "资源总览",
      "审计日志",
    ]);
  });

  it("搭建者只看到模型服务", () => {
    const menu = buildMenu(BUILDER);
    expect(menu.map((group) => group.label)).toEqual(["模型服务"]);
    expect(labels(menu)).toEqual(["模型管理", "模型体验"]);
  });

  it("终端用户没有菜单", () => {
    expect(buildMenu(END_USER)).toEqual([]);
  });

  it("未加载到用户信息时没有菜单", () => {
    expect(buildMenu(undefined)).toEqual([]);
  });
});
