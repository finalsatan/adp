import { run, type Database } from '../db/index.js';
import { newId, nowIso, safeJsonParse } from '../lib/util.js';

export function writeAudit(
  db: Database,
  input: {
    orgId?: string | null;
    actor?: { id: string; name: string } | null;
    action: string;
    resourceType: string;
    resourceId?: string | null;
    detail?: unknown;
  },
): void {
  run(
    db,
    `INSERT INTO audit_logs (id, org_id, actor_id, actor_name, action, resource_type, resource_id, detail, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    newId(),
    input.orgId ?? null,
    input.actor?.id ?? null,
    input.actor?.name ?? '未知用户',
    input.action,
    input.resourceType,
    input.resourceId ?? null,
    JSON.stringify(input.detail ?? {}),
    nowIso(),
  );
}

export interface AuditItem {
  id: string;
  orgId: string | null;
  actorName: string;
  action: string;
  resourceType: string;
  resourceId: string | null;
  createdAt: string;
  detail: unknown;
}

export function serializeAudit(row: Record<string, unknown>): AuditItem {
  return {
    id: String(row.id),
    orgId: (row.org_id as string | null) ?? null,
    actorName: String(row.actor_name),
    action: String(row.action),
    resourceType: String(row.resource_type),
    resourceId: (row.resource_id as string | null) ?? null,
    createdAt: String(row.created_at),
    detail: safeJsonParse(String(row.detail ?? '{}'), {}),
  };
}
