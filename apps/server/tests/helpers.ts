import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { FastifyInstance } from 'fastify';
import { createApp, type CreateAppOptions } from '../src/app.js';
import type { Database } from '../src/db/index.js';

export interface TestServer {
  app: FastifyInstance;
  dataDir: string;
  client: TestClient;
  close: () => Promise<void>;
}

export interface InjectResult {
  status: number;
  data: any;
  text: string;
  headers: Record<string, unknown>;
}

export class TestClient {
  private cookie = '';
  private orgId = '';
  readonly app: FastifyInstance;

  constructor(app: FastifyInstance) {
    this.app = app;
  }

  setOrg(orgId: string): void {
    this.orgId = orgId;
  }

  clearOrg(): void {
    this.orgId = '';
  }

  async request(
    method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE',
    url: string,
    payload?: unknown,
    options: { headers?: Record<string, string>; expected?: number } = {},
  ): Promise<InjectResult> {
    const headers: Record<string, string> = { ...(options.headers ?? {}) };
    if (this.cookie) headers.cookie = this.cookie;
    if (this.orgId) headers['x-org-id'] = this.orgId;
    const response = await this.app.inject({
      method,
      url,
      headers,
      ...(payload === undefined ? {} : { payload: payload as never }),
    });
    const setCookie = response.headers['set-cookie'];
    if (setCookie) {
      const values = Array.isArray(setCookie) ? setCookie : [setCookie];
      this.cookie = values.map((value) => value.split(';')[0]).join('; ');
    }
    let data: any;
    try {
      data = response.body ? JSON.parse(response.body) : undefined;
    } catch {
      data = response.body;
    }
    if (options.expected !== undefined && response.statusCode !== options.expected) {
      throw new Error(`${method} ${url}: expected ${options.expected}, got ${response.statusCode}: ${response.body.slice(0, 1200)}`);
    }
    if (options.expected === undefined && response.statusCode >= 400) {
      throw new Error(`${method} ${url}: ${response.statusCode} ${response.body.slice(0, 1200)}`);
    }
    return {
      status: response.statusCode,
      data,
      text: response.body,
      headers: response.headers,
    };
  }

  get rawCookie(): string {
    return this.cookie;
  }
}

export async function startTestServer(
  overrides: Partial<CreateAppOptions> = {},
): Promise<TestServer> {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'adp-test-'));
  const app = await createApp({
    dataDir,
    autoStartWorker: true,
    workerIntervalMs: 10,
    retryDelayMs: 10,
    logger: false,
    ...overrides,
  });
  await app.ready();
  return {
    app,
    dataDir,
    client: new TestClient(app),
    close: async () => {
      await app.close();
      fs.rmSync(dataDir, { recursive: true, force: true });
    },
  };
}

export async function setupAdmin(client: TestClient, email = 'admin@example.test') {
  const result = await client.request('POST', '/api/auth/setup', {
    name: '测试管理员',
    email,
    password: 'Password123!',
    orgName: '测试组织',
  });
  client.setOrg(result.data.organizations[0].id);
  return result.data as {
    user: { id: string; email: string; isSystemAdmin: boolean };
    organizations: Array<{ id: string; name: string; permissions: string[] }>;
  };
}

export function dbOf(server: TestServer): Database {
  return server.app.adp.db;
}

export async function waitFor<T>(
  fn: () => Promise<T>,
  predicate: (value: T) => boolean,
  timeoutMs = 20_000,
  intervalMs = 25,
): Promise<T> {
  const started = Date.now();
  let latest: T | undefined;
  while (Date.now() - started < timeoutMs) {
    latest = await fn();
    if (predicate(latest)) return latest;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`waitFor timeout: ${JSON.stringify(latest)}`);
}

export async function uploadTextDocument(
  client: TestClient,
  kbId: string,
  name = 'policy.md',
  content = '# 差旅\n\n一线城市住宿上限为每人每晚600元。',
) {
  return client.request('POST', `/api/knowledge-bases/${kbId}/documents`, { name, content });
}

export async function createDemoKnowledgeBase(client: TestClient, name = '测试知识库') {
  const models = (await client.request('GET', '/api/models')).data.items as Array<{
    id: string;
    kind: string;
    provider: string;
  }>;
  const embedding = models.find((model) => model.kind === 'embedding' && model.provider === 'demo')!;
  const chat = models.find((model) => model.kind === 'chat' && model.provider === 'demo')!;
  const kb = await client.request('POST', '/api/knowledge-bases', {
    name,
    visibility: 'organization',
    embeddingModelId: embedding.id,
    chunkConfig: { strategy: 'structure', maxChars: 500, overlap: 50 },
    retrievalConfig: { mode: 'keyword', topK: 3, threshold: 0 },
  });
  return { kb: kb.data, embedding, chat };
}
