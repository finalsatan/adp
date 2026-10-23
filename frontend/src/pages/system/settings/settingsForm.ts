import { isApiError } from "../../../api/errors";
import type { Settings, SettingsPatch } from "../../../api/settings";

/** 系统设置表单的值；白名单在表单里是多行文本。 */
export interface SettingsFormValues {
  password: { min_length: number; min_classes: number };
  login: { lock_threshold: number; lock_minutes: number };
  session: { access_minutes: number; refresh_days: number };
  model_call: { ingest_rate_ratio: number; model_host_allowlist: string };
}

/** 多行文本 → 白名单数组：按行拆分、去首尾空格、丢弃空行。 */
export function textToAllowlist(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

/** 白名单数组 → 多行文本。 */
export function allowlistToText(list: string[]): string {
  return list.join("\n");
}

function sameStrings(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

/** 接口返回的设置 → 表单初始值。 */
export function settingsToForm(settings: Settings): SettingsFormValues {
  return {
    password: { ...settings.password },
    login: { ...settings.login },
    session: { ...settings.session },
    model_call: {
      ingest_rate_ratio: settings.model_call.ingest_rate_ratio,
      model_host_allowlist: allowlistToText(
        settings.model_call.model_host_allowlist ?? [],
      ),
    },
  };
}

/** 只保留改动过的字段，按分组合并成 PATCH 请求体。 */
export function diffSettings(
  settings: Settings,
  values: SettingsFormValues,
): SettingsPatch {
  const patch: SettingsPatch = {};

  const password: NonNullable<SettingsPatch["password"]> = {};
  if (values.password.min_length !== settings.password.min_length) {
    password.min_length = values.password.min_length;
  }
  if (values.password.min_classes !== settings.password.min_classes) {
    password.min_classes = values.password.min_classes;
  }
  if (Object.keys(password).length > 0) {
    patch.password = password;
  }

  const login: NonNullable<SettingsPatch["login"]> = {};
  if (values.login.lock_threshold !== settings.login.lock_threshold) {
    login.lock_threshold = values.login.lock_threshold;
  }
  if (values.login.lock_minutes !== settings.login.lock_minutes) {
    login.lock_minutes = values.login.lock_minutes;
  }
  if (Object.keys(login).length > 0) {
    patch.login = login;
  }

  const session: NonNullable<SettingsPatch["session"]> = {};
  if (values.session.access_minutes !== settings.session.access_minutes) {
    session.access_minutes = values.session.access_minutes;
  }
  if (values.session.refresh_days !== settings.session.refresh_days) {
    session.refresh_days = values.session.refresh_days;
  }
  if (Object.keys(session).length > 0) {
    patch.session = session;
  }

  const modelCall: NonNullable<SettingsPatch["model_call"]> = {};
  if (
    values.model_call.ingest_rate_ratio !==
    settings.model_call.ingest_rate_ratio
  ) {
    modelCall.ingest_rate_ratio = values.model_call.ingest_rate_ratio;
  }
  const allowlist = textToAllowlist(values.model_call.model_host_allowlist);
  if (!sameStrings(allowlist, settings.model_call.model_host_allowlist ?? [])) {
    modelCall.model_host_allowlist = allowlist;
  }
  if (Object.keys(modelCall).length > 0) {
    patch.model_call = modelCall;
  }

  return patch;
}

export function hasChanges(patch: SettingsPatch): boolean {
  return Object.keys(patch).length > 0;
}

interface ValidationItem {
  loc?: unknown;
  msg?: unknown;
}

/** 取 Pydantic 错误定位里的分组和字段名，去掉 body 等位置前缀和列表下标。 */
function toFormPath(loc: unknown): string[] {
  if (!Array.isArray(loc)) return [];
  return loc.filter(
    (part): part is string =>
      typeof part === "string" &&
      part !== "body" &&
      part !== "query" &&
      part !== "path",
  );
}

/** 把后端 422 的 detail.errors 映射成“分组.字段 → 提示”，供表单显示。 */
export function parseSettingsErrors(error: unknown): Record<string, string> {
  if (!isApiError(error) || error.status !== 422) return {};
  const detail = error.detail as { errors?: unknown } | undefined;
  const items: ValidationItem[] = Array.isArray(detail?.errors)
    ? (detail.errors as ValidationItem[])
    : [];
  const result: Record<string, string> = {};
  for (const item of items) {
    const name = toFormPath(item.loc);
    if (name.length === 0) continue;
    const msg =
      typeof item.msg === "string" && item.msg.length > 0
        ? item.msg
        : "参数不正确";
    result[name.join(".")] = msg;
  }
  return result;
}
