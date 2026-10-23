import { describe, expect, it } from "vitest";

import { formatActorOrg, formatTarget } from "./auditFormat";

describe("formatTarget", () => {
  it("没有 detail.from/to 时只用对象描述", () => {
    expect(formatTarget({ target_desc: "用户 chenjiu", detail: null })).toBe(
      "用户 chenjiu",
    );
    expect(
      formatTarget({ target_desc: "员工制度库", detail: { user: "qianjiu" } }),
    ).toBe("员工制度库");
  });

  it("有 from/to 时拼成“名称：A → B”，代码转成中文名", () => {
    expect(
      formatTarget({
        target_desc: "用户 chenjiu",
        detail: { from: "builder", to: "org_admin" },
      }),
    ).toBe("用户 chenjiu：搭建者 → 组织管理员");
    expect(
      formatTarget({
        target_desc: "报销助手",
        detail: { from: "org", to: "platform" },
      }),
    ).toBe("报销助手：本组织 → 全平台");
  });

  it("只有 from 或 to 时不拼接", () => {
    expect(
      formatTarget({ target_desc: "员工制度库", detail: { from: "org" } }),
    ).toBe("员工制度库");
  });

  it("from/to 不是标量（如组织对象）时不拼接", () => {
    expect(
      formatTarget({
        target_desc: "用户 chenjiu",
        detail: { from: { id: 1, name: "研发中心" }, to: { id: 2, name: "运营部" } },
      }),
    ).toBe("用户 chenjiu");
  });

  it("认不出的值原样显示", () => {
    expect(
      formatTarget({
        target_desc: "入库方案 PDF",
        detail: { from: "kb", to: "scheme" },
      }),
    ).toBe("入库方案 PDF：kb → scheme");
  });
});

describe("formatActorOrg", () => {
  it("为空时显示系统管理员", () => {
    expect(formatActorOrg(null)).toBe("—（系统管理员）");
    expect(formatActorOrg("研发中心")).toBe("研发中心");
  });
});
