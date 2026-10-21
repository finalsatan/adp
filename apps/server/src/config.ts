import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
// src/config.ts and dist/config.js are both two levels below the repository root.
export const repositoryRoot = path.resolve(here, '../../..');

export interface AppConfig {
  dataDir: string;
  port: number;
  host: string;
  nodeEnv: string;
  cookieSecure: boolean;
  sessionTtlMs: number;
  parserUrl: string;
  parserToken?: string;
  parserTimeoutMs: number;
  modelTimeoutMs: number;
  encryptionKey?: string;
  publicBaseUrl?: string;
  brandName: string;
  brandSubtitle: string;
  oidc?: {
    issuer: string;
    clientId: string;
    clientSecret: string;
    redirectUri: string;
    scopes: string;
    defaultOrgId?: string;
  };
}

function numberFromEnv(value: string | undefined, fallback: number): number {
  if (!value) return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function loadConfig(overrides: Partial<AppConfig> = {}): AppConfig {
  const env = process.env;
  const nodeEnv = env.NODE_ENV ?? 'development';
  const rawDataDir = overrides.dataDir ?? env.DATA_DIR ?? path.join(repositoryRoot, 'data');
  // npm workspaces change cwd to apps/server. Relative DATA_DIR values must
  // still resolve against the repository root so dev/start share one database.
  const dataDir = path.isAbsolute(rawDataDir) ? rawDataDir : path.resolve(repositoryRoot, rawDataDir);
  const oidcIssuer = env.OIDC_ISSUER?.trim();
  const oidcClientId = env.OIDC_CLIENT_ID?.trim();
  const oidcClientSecret = env.OIDC_CLIENT_SECRET?.trim();

  const config: AppConfig = {
    dataDir,
    port: numberFromEnv(env.PORT, 3001),
    host: env.HOST ?? '0.0.0.0',
    nodeEnv,
    cookieSecure: env.COOKIE_SECURE
      ? env.COOKIE_SECURE === 'true'
      : nodeEnv === 'production',
    sessionTtlMs: numberFromEnv(env.SESSION_TTL_MS, 7 * 24 * 60 * 60 * 1000),
    parserUrl: env.PARSER_URL ?? 'http://127.0.0.1:8001',
    parserToken: env.PARSER_TOKEN || undefined,
    parserTimeoutMs: numberFromEnv(env.PARSER_TIMEOUT_MS, 60_000),
    modelTimeoutMs: numberFromEnv(env.MODEL_TIMEOUT_MS, 45_000),
    encryptionKey: env.APP_MASTER_KEY || env.ENCRYPTION_KEY || undefined,
    publicBaseUrl: env.PUBLIC_BASE_URL || undefined,
    brandName: env.BRAND_NAME ?? 'ADP',
    brandSubtitle: env.BRAND_SUBTITLE ?? '智能体开发平台',
    oidc:
      oidcIssuer && oidcClientId && oidcClientSecret
        ? {
            issuer: oidcIssuer.replace(/\/+$/, ''),
            clientId: oidcClientId,
            clientSecret: oidcClientSecret,
            redirectUri:
              env.OIDC_REDIRECT_URI ??
              `http://localhost:${numberFromEnv(env.PORT, 3001)}/api/auth/oidc/callback`,
            scopes: env.OIDC_SCOPES ?? 'openid profile email',
            defaultOrgId: env.OIDC_DEFAULT_ORG_ID || undefined,
          }
        : undefined,
    ...overrides,
  };
  return config;
}
