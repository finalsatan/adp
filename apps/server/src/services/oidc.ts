import crypto from 'node:crypto';
import { createRemoteJWKSet, jwtVerify, type JWTPayload } from 'jose';
import { all, get, run, type Database, type OrganizationRow, type UserRow } from '../db/index.js';
import type { AppConfig } from '../config.js';
import { AppError } from '../lib/errors.js';
import { newId, nowIso, randomToken } from '../lib/util.js';
import { createOrganization, createUser, ensureMemberRole } from './seed.js';

interface OidcDiscovery {
  authorization_endpoint: string;
  token_endpoint: string;
  jwks_uri: string;
  userinfo_endpoint?: string;
  issuer: string;
}

const discoveryCache = new Map<string, OidcDiscovery>();
const jwksCache = new Map<string, ReturnType<typeof createRemoteJWKSet>>();

async function discover(issuer: string): Promise<OidcDiscovery> {
  const cached = discoveryCache.get(issuer);
  if (cached) return cached;
  let response: Response;
  try {
    response = await fetch(`${issuer}/.well-known/openid-configuration`, {
      signal: AbortSignal.timeout(10_000),
    });
  } catch (error) {
    throw new AppError(502, 'OIDC_DISCOVERY_FAILED', `OIDC 发现失败：${error instanceof Error ? error.message : String(error)}`);
  }
  if (!response.ok) {
    throw new AppError(502, 'OIDC_DISCOVERY_FAILED', `OIDC 发现返回 ${response.status}`);
  }
  const payload = (await response.json()) as Partial<OidcDiscovery>;
  if (!payload.authorization_endpoint || !payload.token_endpoint || !payload.jwks_uri) {
    throw new AppError(502, 'OIDC_DISCOVERY_FAILED', 'OIDC 发现文档缺少必要端点');
  }
  const discoveredIssuer = (payload.issuer ?? '').replace(/\/+$/, '');
  if (discoveredIssuer !== issuer.replace(/\/+$/, '')) {
    throw new AppError(502, 'OIDC_DISCOVERY_FAILED', 'OIDC 发现文档的 issuer 与配置不一致');
  }
  const discovery: OidcDiscovery = {
    authorization_endpoint: payload.authorization_endpoint,
    token_endpoint: payload.token_endpoint,
    jwks_uri: payload.jwks_uri,
    userinfo_endpoint: payload.userinfo_endpoint,
    issuer: discoveredIssuer,
  };
  discoveryCache.set(issuer, discovery);
  return discovery;
}

export async function startOidc(db: Database, config: AppConfig): Promise<{ url: string; state: string }> {
  if (!config.oidc) throw new AppError(404, 'OIDC_NOT_CONFIGURED', '未配置 OIDC 登录');
  const discovery = await discover(config.oidc.issuer);
  run(db, 'DELETE FROM oidc_states WHERE created_at < ?', new Date(Date.now() - 15 * 60 * 1000).toISOString());
  const state = randomToken(24);
  const nonce = randomToken(24);
  const verifier = randomToken(32);
  const challenge = crypto.createHash('sha256').update(verifier).digest('base64url');
  run(
    db,
    'INSERT INTO oidc_states (state, nonce, code_verifier, redirect_uri, created_at) VALUES (?, ?, ?, ?, ?)',
    state,
    nonce,
    verifier,
    config.oidc.redirectUri,
    nowIso(),
  );
  const url = new URL(discovery.authorization_endpoint);
  url.searchParams.set('response_type', 'code');
  url.searchParams.set('client_id', config.oidc.clientId);
  url.searchParams.set('redirect_uri', config.oidc.redirectUri);
  url.searchParams.set('scope', config.oidc.scopes);
  url.searchParams.set('state', state);
  url.searchParams.set('nonce', nonce);
  url.searchParams.set('code_challenge', challenge);
  url.searchParams.set('code_challenge_method', 'S256');
  return { url: url.toString(), state };
}

async function exchangeCode(
  config: AppConfig,
  discovery: OidcDiscovery,
  code: string,
  redirectUri: string,
  verifier: string,
): Promise<{ idToken: string; accessToken?: string }> {
  if (!config.oidc) throw new AppError(404, 'OIDC_NOT_CONFIGURED', '未配置 OIDC 登录');
  const form = new URLSearchParams({
    grant_type: 'authorization_code',
    code,
    redirect_uri: redirectUri,
    client_id: config.oidc.clientId,
    client_secret: config.oidc.clientSecret,
    code_verifier: verifier,
  });
  const doRequest = async (headers: Record<string, string>) =>
    fetch(discovery.token_endpoint, {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded', ...headers },
      body: form.toString(),
      signal: AbortSignal.timeout(15_000),
    });
  let response = await doRequest({});
  if (!response.ok && (response.status === 400 || response.status === 401)) {
    const basic = Buffer.from(`${config.oidc.clientId}:${config.oidc.clientSecret}`).toString('base64');
    response = await doRequest({ authorization: `Basic ${basic}` });
  }
  if (!response.ok) {
    const detail = (await response.text().catch(() => '')).slice(0, 500);
    throw new AppError(502, 'OIDC_TOKEN_FAILED', `OIDC 换取令牌失败 ${response.status}${detail ? `：${detail}` : ''}`);
  }
  const payload = (await response.json()) as { id_token?: string; access_token?: string };
  if (!payload.id_token) throw new AppError(502, 'OIDC_TOKEN_FAILED', 'OIDC 响应中没有 id_token');
  return { idToken: payload.id_token, accessToken: payload.access_token };
}

async function fetchUserinfo(
  accessToken: string,
  endpoint: string,
): Promise<{ email?: string; name?: string; sub?: string } | undefined> {
  try {
    const response = await fetch(endpoint, {
      headers: { authorization: `Bearer ${accessToken}` },
      signal: AbortSignal.timeout(10_000),
    });
    if (!response.ok) return undefined;
    return (await response.json()) as { email?: string; name?: string; sub?: string };
  } catch {
    return undefined;
  }
}

export async function handleOidcCallback(
  db: Database,
  config: AppConfig,
  code: string,
  state: string,
): Promise<UserRow> {
  if (!config.oidc) throw new AppError(404, 'OIDC_NOT_CONFIGURED', '未配置 OIDC 登录');
  const saved = get<{ state: string; nonce: string; code_verifier: string; redirect_uri: string; created_at: string }>(
    db,
    'SELECT * FROM oidc_states WHERE state = ?',
    state,
  );
  if (!saved) throw new AppError(400, 'OIDC_STATE_INVALID', 'OIDC state 无效或已过期');
  run(db, 'DELETE FROM oidc_states WHERE state = ?', state);
  if (Date.parse(saved.created_at) < Date.now() - 15 * 60 * 1000) {
    throw new AppError(400, 'OIDC_STATE_INVALID', 'OIDC state 已过期');
  }
  const discovery = await discover(config.oidc.issuer);
  const tokens = await exchangeCode(config, discovery, code, saved.redirect_uri, saved.code_verifier);
  const jwks = jwksCache.get(discovery.jwks_uri) ?? createRemoteJWKSet(new URL(discovery.jwks_uri));
  jwksCache.set(discovery.jwks_uri, jwks);
  let claims: JWTPayload;
  try {
    const verified = await jwtVerify(tokens.idToken, jwks, {
      issuer: discovery.issuer,
      audience: config.oidc.clientId,
      algorithms: ['RS256', 'RS384', 'RS512', 'ES256', 'ES384', 'PS256', 'PS384', 'PS512'],
    });
    claims = verified.payload;
  } catch (error) {
    throw new AppError(401, 'OIDC_TOKEN_INVALID', `OIDC id_token 校验失败：${error instanceof Error ? error.message : String(error)}`);
  }
  if (claims.nonce !== saved.nonce) throw new AppError(401, 'OIDC_NONCE_INVALID', 'OIDC nonce 不匹配');
  let email = typeof claims.email === 'string' ? claims.email.trim().toLowerCase() : '';
  let name =
    (typeof claims.name === 'string' && claims.name.trim()) ||
    (typeof claims.preferred_username === 'string' && claims.preferred_username.trim()) ||
    email.split('@')[0] ||
    'OIDC 用户';
  const subject = String(claims.sub ?? '');
  if (!subject) throw new AppError(401, 'OIDC_SUBJECT_MISSING', 'OIDC id_token 缺少 sub');
  const emailVerified = claims.email_verified === true;
  if (!email && discovery.userinfo_endpoint && tokens.accessToken) {
    const info = await fetchUserinfo(tokens.accessToken, discovery.userinfo_endpoint);
    if (info?.sub && info.sub !== subject) {
      throw new AppError(401, 'OIDC_SUBJECT_MISMATCH', 'OIDC userinfo.sub 与 id_token.sub 不一致');
    }
    email = info?.email?.trim().toLowerCase() ?? '';
    name = info?.name?.trim() || name;
  } else if (discovery.userinfo_endpoint && tokens.accessToken) {
    // Verify sub even when email is already present so identities cannot be mixed.
    const info = await fetchUserinfo(tokens.accessToken, discovery.userinfo_endpoint);
    if (info?.sub && info.sub !== subject) {
      throw new AppError(401, 'OIDC_SUBJECT_MISMATCH', 'OIDC userinfo.sub 与 id_token.sub 不一致');
    }
  }
  if (!email) throw new AppError(401, 'OIDC_EMAIL_MISSING', 'OIDC 未返回邮箱，无法关联用户');
  if (!emailVerified) {
    throw new AppError(401, 'OIDC_EMAIL_UNVERIFIED', 'OIDC 邮箱未验证，拒绝自动关联或创建用户');
  }

  let user =
    get<UserRow>(db, 'SELECT * FROM users WHERE oidc_subject = ?', subject) ??
    get<UserRow>(db, 'SELECT * FROM users WHERE email = ?', email);
  if (user) {
    if (!user.oidc_subject) run(db, 'UPDATE users SET oidc_subject = ? WHERE id = ?', subject, user.id);
    user = get<UserRow>(db, 'SELECT * FROM users WHERE id = ?', user.id)!;
  } else {
    const defaultOrg = config.oidc.defaultOrgId
      ? get<OrganizationRow>(db, 'SELECT * FROM organizations WHERE id = ?', config.oidc.defaultOrgId)
      : undefined;
    user = createUser(db, { name, email, password: '' });
    run(db, 'UPDATE users SET oidc_subject = ? WHERE id = ?', subject, user.id);
    user = get<UserRow>(db, 'SELECT * FROM users WHERE id = ?', user.id)!;
    if (defaultOrg) {
      const role = ensureMemberRole(db, defaultOrg.id);
      run(
        db,
        'INSERT INTO memberships (id, org_id, user_id, role_id, created_at) VALUES (?, ?, ?, ?, ?)',
        newId(),
        defaultOrg.id,
        user.id,
        role.id,
        nowIso(),
      );
    }
  }
  const memberships = all<{ org_id: string }>(db, 'SELECT org_id FROM memberships WHERE user_id = ?', user.id);
  if (memberships.length === 0) {
    const defaultOrg = config.oidc.defaultOrgId
      ? get<OrganizationRow>(db, 'SELECT * FROM organizations WHERE id = ?', config.oidc.defaultOrgId)
      : all<OrganizationRow>(db, 'SELECT * FROM organizations ORDER BY created_at LIMIT 1')[0];
    if (defaultOrg) {
      const role = ensureMemberRole(db, defaultOrg.id);
      run(
        db,
        'INSERT INTO memberships (id, org_id, user_id, role_id, created_at) VALUES (?, ?, ?, ?, ?)',
        newId(),
        defaultOrg.id,
        user.id,
        role.id,
        nowIso(),
      );
    } else {
      createOrganization(db, {
        name: `${name}的组织`,
        creatorId: user.id,
        addCreatorAsAdmin: true,
      });
    }
  }
  return user;
}
