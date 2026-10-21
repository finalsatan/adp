import type { FastifyReply, FastifyRequest } from 'fastify';
import {
  get,
  run,
  all,
  type AppRow,
  type Database,
  type UserRow,
} from '../db/index.js';
import type { AppConfig } from '../config.js';
import { AppError, forbidden, unauthorized } from '../lib/errors.js';
import { newId, nowIso, randomToken, sha256 } from '../lib/util.js';
import {
  buildCaller,
  listUserOrganizations,
  resolveCurrentOrg,
  toAuthUser,
  type AppKeyCaller,
  type Caller,
  type SessionCaller,
} from '../services/access.js';

export const SESSION_COOKIE = 'adp_session';

export interface AuthLoadResult {
  caller?: Caller;
  error?: AppError;
}

function requestedOrgId(request: FastifyRequest): string | undefined {
  const header = request.headers['x-org-id'];
  if (typeof header === 'string' && header.trim()) return header.trim();
  return undefined;
}

export function loadSessionCaller(
  db: Database,
  request: FastifyRequest,
): { caller?: SessionCaller; error?: AppError } {
  const raw = request.cookies[SESSION_COOKIE];
  if (!raw) return {};
  const tokenHash = sha256(raw);
  const session = get<{
    token_hash: string;
    user_id: string;
    org_id: string | null;
    expires_at: string;
  }>(db, 'SELECT * FROM sessions WHERE token_hash = ?', tokenHash);
  if (!session) return {};
  if (Date.parse(session.expires_at) <= Date.now()) {
    run(db, 'DELETE FROM sessions WHERE token_hash = ?', tokenHash);
    return {};
  }
  const user = get<UserRow>(db, 'SELECT * FROM users WHERE id = ?', session.user_id);
  if (!user) {
    run(db, 'DELETE FROM sessions WHERE token_hash = ?', tokenHash);
    return {};
  }
  const organizations = listUserOrganizations(db, user);
  const requested = requestedOrgId(request);
  const org = resolveCurrentOrg(organizations, session.org_id, requested);
  if (requested && !org) {
    return { error: forbidden('无权切换到该组织') };
  }
  if (org && org.id !== session.org_id) {
    run(db, 'UPDATE sessions SET org_id = ? WHERE token_hash = ?', org.id, tokenHash);
  }
  return { caller: buildCaller(user, organizations, org, tokenHash) };
}

export function loadAppKeyCaller(
  db: Database,
  request: FastifyRequest,
): { caller?: AppKeyCaller; error?: AppError } {
  const authorization = request.headers.authorization;
  if (!authorization?.startsWith('Bearer ')) return {};
  const raw = authorization.slice('Bearer '.length).trim();
  if (!raw.startsWith('adp_')) return { error: unauthorized('API Key 格式无效') };
  const keyHash = sha256(raw);
  const key = get<{
    id: string;
    app_id: string;
    created_by: string;
    revoked_at: string | null;
  }>(db, 'SELECT * FROM api_keys WHERE key_hash = ?', keyHash);
  if (!key) return { error: unauthorized('API Key 无效') };
  if (key.revoked_at) return { error: unauthorized('API Key 已吊销') };
  const app = get<AppRow>(db, 'SELECT * FROM apps WHERE id = ?', key.app_id);
  if (!app) return { error: unauthorized('API Key 绑定的应用不存在') };
  const user = get<UserRow>(db, 'SELECT * FROM users WHERE id = ?', key.created_by);
  if (!user) return { error: unauthorized('API Key 的创建者已不存在') };
  const organizations = listUserOrganizations(db, user);
  const org = organizations.find((item) => item.id === app.org_id);
  return {
    caller: {
      kind: 'appkey',
      user: toAuthUser(user),
      userRow: user,
      orgId: app.org_id,
      organizations,
      permissions: new Set(org?.permissions ?? []),
      appId: app.id,
      keyId: key.id,
    },
  };
}

export function loadRequestAuth(db: Database, request: FastifyRequest): AuthLoadResult {
  const appKey = loadAppKeyCaller(db, request);
  if (request.headers.authorization) {
    if (appKey.error) return appKey;
    if (appKey.caller) return appKey;
    return { error: unauthorized('Authorization 仅支持绑定应用的 API Key') };
  }
  return loadSessionCaller(db, request);
}

export function setSessionCookie(reply: FastifyReply, rawToken: string, config: AppConfig): void {
  reply.setCookie(SESSION_COOKIE, rawToken, {
    httpOnly: true,
    sameSite: 'lax',
    secure: config.cookieSecure,
    path: '/',
    maxAge: Math.floor(config.sessionTtlMs / 1000),
  });
}

export function clearSessionCookie(reply: FastifyReply, config: AppConfig): void {
  reply.clearCookie(SESSION_COOKIE, {
    httpOnly: true,
    sameSite: 'lax',
    secure: config.cookieSecure,
    path: '/',
  });
}

export function issueSession(
  db: Database,
  reply: FastifyReply,
  config: AppConfig,
  user: UserRow,
  orgId: string | null,
): string {
  const rawToken = randomToken(32);
  run(
    db,
    'INSERT INTO sessions (token_hash, user_id, org_id, created_at, expires_at) VALUES (?, ?, ?, ?, ?)',
    sha256(rawToken),
    user.id,
    orgId,
    nowIso(),
    new Date(Date.now() + config.sessionTtlMs).toISOString(),
  );
  setSessionCookie(reply, rawToken, config);
  return rawToken;
}

export function destroySession(db: Database, reply: FastifyReply, config: AppConfig, request: FastifyRequest): void {
  const raw = request.cookies[SESSION_COOKIE];
  if (raw) run(db, 'DELETE FROM sessions WHERE token_hash = ?', sha256(raw));
  clearSessionCookie(reply, config);
}

export function sessionOrganizations(db: Database, user: UserRow) {
  return listUserOrganizations(db, user);
}

export function cleanupExpiredSessions(db: Database): void {
  run(db, 'DELETE FROM sessions WHERE expires_at <= ?', nowIso());
}

export function countUsers(db: Database): number {
  return get<{ n: number }>(db, 'SELECT COUNT(*) AS n FROM users')?.n ?? 0;
}

export function usersForCurrentOrg(db: Database, caller: Caller): UserRow[] {
  if (caller.user.isSystemAdmin) {
    return all<UserRow>(db, 'SELECT * FROM users ORDER BY created_at ASC');
  }
  if (!caller.orgId) return [];
  return all<UserRow>(
    db,
    `SELECT DISTINCT u.* FROM users u
     JOIN memberships m ON m.user_id = u.id
     WHERE m.org_id = ?
     ORDER BY u.created_at ASC`,
    caller.orgId,
  );
}

export function applySessionCookieForTests(reply: FastifyReply, token: string): void {
  reply.header('x-session-token', token);
}
