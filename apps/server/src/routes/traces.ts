import type { FastifyInstance } from 'fastify';
import { all, get, type TraceRow } from '../db/index.js';
import type { ServerServices } from '../context.js';
import { forbidden, notFound } from '../lib/errors.js';
import { pageOptions, parseParams, requireSessionCaller } from '../lib/http.js';
import { idParams } from '../lib/schemas.js';
import { serializeTrace, traceWithSpans } from '../services/trace.js';

export async function registerTraceRoutes(app: FastifyInstance, services: ServerServices): Promise<void> {
  const { db } = services;

  app.get('/api/traces', async (request) => {
    const caller = requireSessionCaller(request);
    if (!caller.user.isSystemAdmin && !caller.permissions.has('observe:read')) {
      throw forbidden('缺少 observe:read 权限');
    }
    const { limit, offset } = pageOptions(request, 100);
    const query = request.query as { kind?: string; status?: string; appId?: string };
    const where: string[] = [];
    const params: unknown[] = [];
    if (!caller.user.isSystemAdmin) {
      where.push('org_id = ?');
      params.push(caller.orgId);
    }
    if (query.kind) {
      where.push('kind = ?');
      params.push(query.kind);
    }
    if (query.status) {
      where.push('status = ?');
      params.push(query.status);
    }
    if (query.appId) {
      where.push('app_id = ?');
      params.push(query.appId);
    }
    const sql = `SELECT * FROM traces ${where.length ? `WHERE ${where.join(' AND ')}` : ''} ORDER BY created_at DESC LIMIT ? OFFSET ?`;
    const rows = all<TraceRow>(db, sql, ...params, limit, offset);
    return { items: rows.map(serializeTrace) };
  });

  app.get('/api/traces/:id', async (request) => {
    const caller = requireSessionCaller(request);
    if (!caller.user.isSystemAdmin && !caller.permissions.has('observe:read')) {
      throw forbidden('缺少 observe:read 权限');
    }
    const { id } = parseParams(request, idParams);
    const row = get<TraceRow>(db, 'SELECT * FROM traces WHERE id = ?', id);
    if (!row) throw notFound('追踪记录');
    if (!caller.user.isSystemAdmin && row.org_id !== caller.orgId) throw notFound('追踪记录');
    return traceWithSpans(db, row);
  });
}
