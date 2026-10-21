import type { FastifyInstance } from 'fastify';
import { all, get, run, type ApiKeyRow, type AppRow } from '../db/index.js';
import type { ServerServices } from '../context.js';
import { conflict, forbidden, notFound } from '../lib/errors.js';
import { parseBody, parseParams, requireSessionCaller } from '../lib/http.js';
import { apiKeyCreateSchema, idParams } from '../lib/schemas.js';
import { newId, nowIso, randomToken, sha256 } from '../lib/util.js';
import { appAccess, permissionsForOrg } from '../services/access.js';
import { writeAudit } from '../services/audit.js';

function serializeKey(row: ApiKeyRow) {
  return {
    id: row.id,
    name: row.name,
    prefix: row.prefix,
    appId: row.app_id,
    createdAt: row.created_at,
    revokedAt: row.revoked_at ?? undefined,
  };
}

export async function registerApiKeyRoutes(app: FastifyInstance, services: ServerServices): Promise<void> {
  const { db } = services;

  app.get('/api/api-keys', async (request) => {
    const caller = requireSessionCaller(request);
    if (!caller.user.isSystemAdmin && !caller.permissions.has('keys:manage')) {
      throw forbidden('缺少 keys:manage 权限');
    }
    const rows = all<ApiKeyRow & { app_name: string }>(
      db,
      caller.user.isSystemAdmin
        ? `SELECT k.*, a.name AS app_name FROM api_keys k JOIN apps a ON a.id = k.app_id ORDER BY k.created_at DESC`
        : `SELECT k.*, a.name AS app_name FROM api_keys k JOIN apps a ON a.id = k.app_id
           WHERE a.org_id = ? ORDER BY k.created_at DESC`,
      ...(caller.user.isSystemAdmin ? [] : [caller.orgId]),
    );
    return {
      items: rows.map((row) => ({ ...serializeKey(row), appName: row.app_name })),
    };
  });

  app.post('/api/api-keys', async (request) => {
    const caller = requireSessionCaller(request);
    const body = parseBody(request, apiKeyCreateSchema);
    const app = get<AppRow>(db, 'SELECT * FROM apps WHERE id = ?', body.appId);
    if (!app) throw notFound('应用');
    const access = appAccess(db, caller, app);
    if (!access.edit) throw forbidden('无权为该应用创建 API Key');
    if (!caller.user.isSystemAdmin && !permissionsForOrg(caller, app.org_id).includes('keys:manage')) {
      throw forbidden('缺少 keys:manage 权限');
    }
    if (app.status !== 'published' || !app.published_version) {
      throw conflict('只能为已发布应用创建 API Key');
    }
    const rawKey = `adp_${randomToken(32)}`;
    const prefix = rawKey.slice(0, 12);
    const id = newId();
    run(
      db,
      `INSERT INTO api_keys (id, name, prefix, key_hash, app_id, created_by, created_at, revoked_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, NULL)`,
      id,
      body.name,
      prefix,
      sha256(rawKey),
      app.id,
      caller.user.id,
      nowIso(),
    );
    writeAudit(db, {
      orgId: app.org_id,
      actor: caller.user,
      action: 'api_key.create',
      resourceType: 'api_key',
      resourceId: id,
      detail: { name: body.name, appId: app.id, prefix },
    });
    return { id, key: rawKey, prefix, appId: app.id, name: body.name, createdAt: nowIso() };
  });

  app.delete('/api/api-keys/:id', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const key = get<ApiKeyRow>(db, 'SELECT * FROM api_keys WHERE id = ?', id);
    if (!key) throw notFound('API Key');
    const app = get<AppRow>(db, 'SELECT * FROM apps WHERE id = ?', key.app_id);
    if (!app) throw notFound('应用');
    if (!caller.user.isSystemAdmin && !permissionsForOrg(caller, app.org_id).includes('keys:manage')) {
      throw forbidden('缺少 keys:manage 权限');
    }
    if (!appAccess(db, caller, app).edit) throw forbidden('无权吊销该应用的 API Key');
    run(db, 'UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL', nowIso(), key.id);
    writeAudit(db, {
      orgId: app.org_id,
      actor: caller.user,
      action: 'api_key.revoke',
      resourceType: 'api_key',
      resourceId: key.id,
      detail: { appId: app.id, prefix: key.prefix },
    });
    return { ok: true };
  });
}
