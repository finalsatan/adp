import type { FastifyInstance } from 'fastify';
import { get } from '../db/index.js';
import type { UserRow } from '../db/index.js';
import type { ServerServices } from '../context.js';
import { AppError, conflict, unauthorized } from '../lib/errors.js';
import { parseBody, requireCaller, requireSessionCaller } from '../lib/http.js';
import { setupSchema, loginSchema } from '../lib/schemas.js';
import { nowIso, verifyPassword } from '../lib/util.js';
import {
  countUsers,
  destroySession,
  issueSession,
  sessionOrganizations,
} from '../services/auth.js';
import { serializeUser } from '../services/serializers.js';
import { initializeFirstUser, seedDemoModels, seedGlobalRoles } from '../services/seed.js';
import { writeAudit } from '../services/audit.js';
import { toAuthUser } from '../services/access.js';
import { handleOidcCallback, startOidc } from '../services/oidc.js';

const OIDC_STATE_COOKIE = 'adp_oidc_state';

export async function registerAuthRoutes(app: FastifyInstance, services: ServerServices): Promise<void> {
  const { db, config } = services;

  app.get('/health', async () => ({ status: 'ok', version: '0.1.0' }));

  app.get('/api/branding', async () => ({
    name: config.brandName,
    subtitle: config.brandSubtitle,
  }));

  app.get('/api/auth/status', async () => ({
    initialized: countUsers(db) > 0,
    oidcEnabled: Boolean(config.oidc),
  }));

  app.post('/api/auth/setup', async (request, reply) => {
    const body = parseBody(request, setupSchema);
    if (countUsers(db) > 0) throw conflict('平台已经初始化，不能重复执行初始化');
    let result: { user: UserRow };
    try {
      seedDemoModels(db);
      seedGlobalRoles(db);
      result = initializeFirstUser(db, body);
    } catch (error) {
      if (error instanceof Error && error.message === 'ALREADY_INITIALIZED') {
        throw conflict('平台已经初始化，不能重复执行初始化');
      }
      throw error;
    }
    const organizations = sessionOrganizations(db, result.user);
    issueSession(db, reply, config, result.user, organizations[0]?.id ?? null);
    writeAudit(db, {
      orgId: organizations[0]?.id,
      actor: result.user,
      action: 'auth.setup',
      resourceType: 'platform',
      detail: { email: result.user.email, orgName: body.orgName },
    });
    return { user: serializeUser(result.user), organizations };
  });

  app.post('/api/auth/login', async (request, reply) => {
    const body = parseBody(request, loginSchema);
    const user = get<UserRow>(db, 'SELECT * FROM users WHERE email = ?', body.email.toLowerCase());
    if (!user || !verifyPassword(body.password, user.password_hash)) {
      throw unauthorized('邮箱或密码不正确');
    }
    const organizations = sessionOrganizations(db, user);
    issueSession(db, reply, config, user, organizations[0]?.id ?? null);
    writeAudit(db, {
      orgId: organizations[0]?.id,
      actor: user,
      action: 'auth.login',
      resourceType: 'user',
      resourceId: user.id,
    });
    return { user: serializeUser(user), organizations };
  });

  app.post('/api/auth/logout', async (request, reply) => {
    const caller = requireSessionCaller(request);
    destroySession(db, reply, config, request);
    writeAudit(db, {
      orgId: caller.orgId,
      actor: caller.user,
      action: 'auth.logout',
      resourceType: 'user',
      resourceId: caller.user.id,
    });
    return { ok: true };
  });

  app.get('/api/auth/me', async (request) => {
    const caller = requireSessionCaller(request);
    return {
      user: serializeUser(caller.userRow),
      organizations: caller.organizations,
      currentOrgId: caller.orgId,
    };
  });

  app.get('/api/auth/oidc/start', async (request, reply) => {
    if (!config.oidc) throw new AppError(404, 'OIDC_NOT_CONFIGURED', '未配置 OIDC 登录');
    const started = await startOidc(db, config);
    reply.setCookie(OIDC_STATE_COOKIE, started.state, {
      httpOnly: true,
      sameSite: 'lax',
      secure: config.cookieSecure,
      path: '/api/auth/oidc/callback',
      maxAge: 10 * 60,
    });
    return reply.redirect(started.url, 302);
  });

  app.get('/api/auth/oidc/callback', async (request, reply) => {
    if (!config.oidc) throw new AppError(404, 'OIDC_NOT_CONFIGURED', '未配置 OIDC 登录');
    const query = request.query as { code?: string; state?: string; error?: string; error_description?: string };
    const cookieState = request.cookies[OIDC_STATE_COOKIE];
    reply.clearCookie(OIDC_STATE_COOKIE, {
      httpOnly: true,
      sameSite: 'lax',
      secure: config.cookieSecure,
      path: '/api/auth/oidc/callback',
    });
    if (query.error) throw new AppError(400, 'OIDC_ERROR', query.error_description || query.error);
    if (!query.code || !query.state) throw new AppError(400, 'OIDC_ERROR', 'OIDC 回调缺少 code 或 state');
    if (!cookieState || cookieState !== query.state) {
      throw new AppError(400, 'OIDC_STATE_INVALID', 'OIDC state 与浏览器绑定 cookie 不一致');
    }
    const user = await handleOidcCallback(db, config, query.code, query.state);
    const organizations = sessionOrganizations(db, user);
    issueSession(db, reply, config, user, organizations[0]?.id ?? null);
    writeAudit(db, {
      orgId: organizations[0]?.id,
      actor: user,
      action: 'auth.oidc.login',
      resourceType: 'user',
      resourceId: user.id,
    });
    return reply.redirect('/', 302);
  });

  app.get('/api/overview', async (request) => {
    const caller = requireSessionCaller(request);
    const orgId = caller.orgId;
    const canObserve = caller.user.isSystemAdmin || caller.permissions.has('observe:read');
    const canSeeKnowledge = caller.user.isSystemAdmin || caller.permissions.has('knowledge:read');
    const canSeeApps = caller.user.isSystemAdmin || caller.permissions.has('apps:read');

    const kbIds = !canSeeKnowledge
      ? []
      : caller.user.isSystemAdmin
        ? db.prepare('SELECT id FROM knowledge_bases').all().map((row) => String((row as { id: string }).id))
        : db
            .prepare(
              `SELECT id FROM knowledge_bases
               WHERE visibility = 'platform'
                  OR (org_id = ? AND (visibility = 'organization' OR owner_id = ?))
                  OR id IN (SELECT kb_id FROM kb_grants WHERE user_id = ?)`,
            )
            .all(orgId, caller.user.id, caller.user.id)
            .map((row) => String((row as { id: string }).id));
    const placeholders = kbIds.map(() => '?').join(',');
    const documentCount = kbIds.length
      ? Number(
          (get<{ n: number }>(db, `SELECT COUNT(*) AS n FROM documents WHERE kb_id IN (${placeholders})`, ...kbIds)?.n ?? 0),
        )
      : 0;
    const chunkCount = kbIds.length
      ? Number(
          (get<{ n: number }>(db, `SELECT COUNT(*) AS n FROM chunks WHERE kb_id IN (${placeholders})`, ...kbIds)?.n ?? 0),
        )
      : 0;
    const appRows = !canSeeApps
      ? []
      : caller.user.isSystemAdmin
        ? db.prepare('SELECT id FROM apps').all()
        : db
            .prepare(
              `SELECT id FROM apps WHERE owner_id = ? OR visibility = 'platform' OR (visibility = 'organization' AND org_id = ?)`,
            )
            .all(caller.user.id, orgId);
    const appIds = appRows.map((row) => String((row as { id: string }).id));
    const appPlaceholders = appIds.map(() => '?').join(',');
    const queries = appIds.length
      ? Number(
          (get<{ n: number }>(
            db,
            canObserve
              ? `SELECT COUNT(*) AS n FROM messages m JOIN conversations c ON c.id = m.conversation_id
                 WHERE m.role = 'user' AND c.app_id IN (${appPlaceholders})`
              : `SELECT COUNT(*) AS n FROM messages m JOIN conversations c ON c.id = m.conversation_id
                 WHERE m.role = 'user' AND c.user_id = ? AND c.app_id IN (${appPlaceholders})`,
            ...(canObserve ? appIds : [caller.user.id, ...appIds]),
          )?.n ?? 0),
        )
      : 0;
    const feedbackPending =
      canObserve && appIds.length
        ? Number(
            (get<{ n: number }>(
              db,
              `SELECT COUNT(*) AS n FROM feedback f
               JOIN messages m ON m.id = f.message_id
               JOIN conversations c ON c.id = m.conversation_id
               WHERE f.rating = -1 AND f.added_to_eval = 0 AND c.app_id IN (${appPlaceholders})`,
              ...appIds,
            )?.n ?? 0),
          )
        : 0;
    const recentTraces = canObserve
      ? (db
          .prepare(
            caller.user.isSystemAdmin
              ? 'SELECT * FROM traces ORDER BY created_at DESC LIMIT 5'
              : 'SELECT * FROM traces WHERE org_id = ? ORDER BY created_at DESC LIMIT 5',
          )
          .all(...(caller.user.isSystemAdmin ? [] : [orgId])) as Array<Record<string, unknown>>)
      : [];
    return {
      knowledgeBases: kbIds.length,
      documents: documentCount,
      chunks: chunkCount,
      apps: appIds.length,
      queries,
      feedbackPending,
      recentTraces: recentTraces.map((row) => ({
        id: String(row.id),
        kind: String(row.kind),
        name: String(row.name),
        status: String(row.status),
        durationMs: Number(row.duration_ms ?? 0),
        tokenCount: row.token_count === null ? undefined : Number(row.token_count),
        appId: row.app_id,
        createdAt: String(row.created_at),
      })),
      currentOrgId: orgId,
      currentUser: toAuthUser(caller.userRow),
      generatedAt: nowIso(),
    };
  });

}
