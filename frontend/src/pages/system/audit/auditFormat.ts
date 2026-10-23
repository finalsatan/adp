import dayjs from "dayjs";

import type { components } from "../../../api/schema";
import {
  RESOURCE_STATUS_LABELS,
  ROLE_LABELS,
  VISIBILITY_LABELS,
} from "../../../lib/labels";

export type AuditLog = components["schemas"]["AuditOut"];

/** 列表里的时间，如 10-21 09:12:03。 */
export function formatAuditTime(at: string): string {
  return dayjs(at).format("MM-DD HH:mm:ss");
}

/** 操作人组织：系统管理员无所属组织，显示“—（系统管理员）”。 */
export function formatActorOrg(name: string | null | undefined): string {
  return name && name.length > 0 ? name : "—（系统管理员）";
}

/**
 * “对象”列：默认用 target_desc；detail 里同时有 from、to 时拼成“名称：A → B”
 * （如“用户 chenjiu：搭建者 → 组织管理员”）。
 */
export function formatTarget(
  record: Pick<AuditLog, "target_desc" | "detail">,
): string {
  const desc = record.target_desc ?? "—";
  const detail = record.detail;
  if (!detail) {
    return desc;
  }
  const from = detail["from"];
  const to = detail["to"];
  if (!isScalar(from) || !isScalar(to)) {
    return desc;
  }
  return `${desc}：${labelValue(from)} → ${labelValue(to)}`;
}

function isScalar(value: unknown): value is string | number {
  return typeof value === "string" || typeof value === "number";
}

/** detail 里存的是代码（如 builder、org），显示时转成中文名；认不出的原样显示。 */
function labelValue(value: string | number): string {
  if (typeof value !== "string") {
    return String(value);
  }
  for (const labels of [ROLE_LABELS, VISIBILITY_LABELS, RESOURCE_STATUS_LABELS]) {
    if (value in labels) {
      return labels[value as keyof typeof labels] ?? value;
    }
  }
  return value;
}
