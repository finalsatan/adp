import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApiError } from "../api/errors";
import {
  PasswordRules,
  evaluatePassword,
  parseWeakReasons,
  type PasswordPolicy,
} from "./PasswordRules";

const POLICY: PasswordPolicy = { password_min_length: 8, password_min_classes: 3 };

function ruleState(password: string, username: string, key: string) {
  const rule = evaluatePassword(password, username, POLICY).find(
    (item) => item.key === key,
  );
  if (!rule) {
    throw new Error(`missing rule ${key}`);
  }
  return rule.ok;
}

describe("evaluatePassword", () => {
  it("按策略判断长度", () => {
    expect(ruleState("Ab1!xyz", "admin", "length")).toBe(false);
    expect(ruleState("Ab1!xyzw", "admin", "length")).toBe(true);
  });

  it("按策略判断字符类别数", () => {
    expect(ruleState("abcdefgh", "admin", "classes")).toBe(false);
    expect(ruleState("abcdefg1", "admin", "classes")).toBe(false);
    expect(ruleState("Abcdefg1", "admin", "classes")).toBe(true);
    expect(ruleState("Abcdefg!", "admin", "classes")).toBe(true);
  });

  it("忽略大小写和首尾空格判断是否与用户名相同", () => {
    expect(ruleState("admin", "admin", "username")).toBe(false);
    expect(ruleState("ADMIN", "admin", "username")).toBe(false);
    expect(ruleState(" Admin ", "admin", "username")).toBe(false);
    expect(ruleState("Admin1!", "admin", "username")).toBe(true);
  });

  it("常见弱密码只能由后端判断", () => {
    expect(ruleState("Abcdefg1", "admin", "common")).toBeNull();
  });
});

describe("parseWeakReasons", () => {
  it("从 WEAK_PASSWORD 的 detail.reasons 取出原因", () => {
    const error = new ApiError(422, {
      code: "WEAK_PASSWORD",
      detail: { reasons: ["TOO_SHORT", "COMMON_PASSWORD"] },
    });
    expect(parseWeakReasons(error)).toEqual(["TOO_SHORT", "COMMON_PASSWORD"]);
  });

  it("其他错误返回空数组", () => {
    expect(parseWeakReasons(new ApiError(400, { code: "WRONG_OLD_PASSWORD" }))).toEqual(
      [],
    );
    expect(parseWeakReasons(new Error("boom"))).toEqual([]);
  });
});

describe("PasswordRules", () => {
  it("命中的后端原因把对应规则标红", () => {
    render(
      <PasswordRules
        password="Abcdefg1"
        username="admin"
        policy={POLICY}
        reasons={["COMMON_PASSWORD"]}
      />,
    );
    expect(screen.getByText("不是常见弱密码")).toHaveClass("ant-typography-danger");
    expect(screen.getByText("不少于 8 位")).not.toHaveClass("ant-typography-danger");
  });

  it("两次提交都不通过时按前端判断标红", () => {
    render(
      <PasswordRules password="abc" username="admin" policy={POLICY} />,
    );
    expect(screen.getByText("不少于 8 位")).toHaveClass("ant-typography-danger");
    expect(screen.getByText("包含大小写、数字、符号中至少 3 类")).toHaveClass(
      "ant-typography-danger",
    );
  });
});
