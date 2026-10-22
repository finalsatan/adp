import {
  CheckCircleFilled,
  CloseCircleFilled,
  MinusCircleOutlined,
} from "@ant-design/icons";
import { Space, Typography } from "antd";

import { isApiError } from "../api/errors";

export interface PasswordPolicy {
  password_min_length: number;
  password_min_classes: number;
}

export type PasswordRuleKey = "length" | "classes" | "username" | "common";

export interface PasswordRule {
  key: PasswordRuleKey;
  label: string;
  /** 前端可判断的规则：true 通过、false 未通过；null 表示只能由后端判断。 */
  ok: boolean | null;
}

/** 后端 WEAK_PASSWORD 的原因代码 → 规则。 */
const REASON_TO_RULE: Record<string, PasswordRuleKey> = {
  TOO_SHORT: "length",
  TOO_FEW_CLASSES: "classes",
  SAME_AS_USERNAME: "username",
  COMMON_PASSWORD: "common",
};

function charClassCount(password: string): number {
  const classes = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/];
  return classes.filter((pattern) => pattern.test(password)).length;
}

/** 前端能判断的三条规则；“不是常见弱密码”只能由后端判断，固定为 null。 */
export function evaluatePassword(
  password: string,
  username: string,
  policy: PasswordPolicy,
): PasswordRule[] {
  return [
    {
      key: "length",
      label: `不少于 ${policy.password_min_length} 位`,
      ok: password.length >= policy.password_min_length,
    },
    {
      key: "classes",
      label: `包含大小写、数字、符号中至少 ${policy.password_min_classes} 类`,
      ok: charClassCount(password) >= policy.password_min_classes,
    },
    {
      key: "username",
      label: "不与用户名相同",
      ok: password.trim().toLowerCase() !== username.trim().toLowerCase(),
    },
    { key: "common", label: "不是常见弱密码", ok: null },
  ];
}

/** 从 WEAK_PASSWORD 错误的 detail.reasons 里取出原因代码。 */
export function parseWeakReasons(error: unknown): string[] {
  if (!isApiError(error) || error.code !== "WEAK_PASSWORD") {
    return [];
  }
  const detail = error.detail;
  if (detail && typeof detail === "object" && "reasons" in detail) {
    const reasons = (detail as { reasons?: unknown }).reasons;
    if (Array.isArray(reasons)) {
      return reasons.filter(
        (reason): reason is string => typeof reason === "string",
      );
    }
  }
  return [];
}

interface PasswordRulesProps {
  password: string;
  username: string;
  policy: PasswordPolicy;
  /** 上次提交后端返回的原因代码（WEAK_PASSWORD）。 */
  reasons?: string[];
}

/** 密码规则清单：前端实时判断，命中的后端原因覆盖为未通过。 */
export function PasswordRules({
  password,
  username,
  policy,
  reasons = [],
}: PasswordRulesProps) {
  const serverRules = new Set(
    reasons
      .map((reason) => REASON_TO_RULE[reason])
      .filter((key): key is PasswordRuleKey => Boolean(key)),
  );

  return (
    <Space direction="vertical" size={4}>
      {evaluatePassword(password, username, policy).map((rule) => {
        const failed = serverRules.has(rule.key) || rule.ok === false;
        const passed = !serverRules.has(rule.key) && rule.ok === true;
        return (
          <Space key={rule.key} size={6}>
            {failed ? (
              <CloseCircleFilled style={{ color: "#ff4d4f" }} />
            ) : passed ? (
              <CheckCircleFilled style={{ color: "#52c41a" }} />
            ) : (
              <MinusCircleOutlined style={{ color: "#bfbfbf" }} />
            )}
            <Typography.Text type={failed ? "danger" : "secondary"}>
              {rule.label}
            </Typography.Text>
          </Space>
        );
      })}
    </Space>
  );
}
