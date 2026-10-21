import fs from 'node:fs';
import path from 'node:path';
import Fastify, { type FastifyInstance, type FastifyServerOptions } from 'fastify';
import cookie from '@fastify/cookie';
import multipart from '@fastify/multipart';
import fastifyStatic from '@fastify/static';
import { ZodError } from 'zod';
import { loadConfig, repositoryRoot, type AppConfig } from './config.js';
import { openDatabase } from './db/index.js';
import { AppError } from './lib/errors.js';
import { redactSecrets, SecretBox } from './lib/util.js';
import type { ServerServices } from './context.js';
import { cleanupExpiredSessions, loadRequestAuth } from './services/auth.js';
import { DocumentFiles, IngestService } from './services/ingest.js';
import { JobWorker } from './services/jobs.js';
import { EvalService } from './services/evaluator.js';
import { seedDemoModels, seedGlobalRoles } from './services/seed.js';
import { registerAuthRoutes } from './routes/auth.js';
import { registerOrganizationRoutes } from './routes/organizations.js';
import { registerModelRoutes } from './routes/models.js';
import { registerKnowledgeRoutes } from './routes/knowledge.js';
import { registerAppRoutes } from './routes/apps.js';
import { registerApiKeyRoutes } from './routes/apiKeys.js';
import { registerTraceRoutes } from './routes/traces.js';
import { registerEvalRoutes } from './routes/evals.js';
import { registerJobRoutes } from './routes/jobs.js';

declare module 'fastify' {
  interface FastifyInstance {
    adp: ServerServices;
  }
}

export interface CreateAppOptions extends Partial<AppConfig> {
  logger?: FastifyServerOptions['logger'];
  autoStartWorker?: boolean;
  workerIntervalMs?: number;
  retryDelayMs?: number;
  webDistDir?: string;
}

function isMultipartLimitError(error: { code?: string; statusCode?: number }): boolean {
  return (
    error.code === 'FST_REQ_FILE_TOO_LARGE' ||
    error.code === 'FST_FILES_LIMIT' ||
    error.code === 'FST_PARTS_LIMIT' ||
    error.statusCode === 413
  );
}

export async function createApp(options: CreateAppOptions = {}): Promise<FastifyInstance> {
  const {
    logger = false,
    autoStartWorker = true,
    workerIntervalMs = 250,
    retryDelayMs = 750,
    webDistDir,
    ...configOverrides
  } = options;
  const config = loadConfig(configOverrides);
  const db = openDatabase(config.dataDir);
  seedDemoModels(db);
  seedGlobalRoles(db);
  cleanupExpiredSessions(db);
  const secretBox = new SecretBox(config.dataDir, config.encryptionKey);
  const files = new DocumentFiles(config.dataDir);
  const worker = new JobWorker(db, { intervalMs: workerIntervalMs, retryDelayMs });
  const evalService = new EvalService({ db, secretBox, config });

  worker.register('ingest', async (context) => {
    const documentId = typeof context.payload.documentId === 'string' ? context.payload.documentId : '';
    if (!documentId) throw new Error('入库任务缺少 documentId');
    const ingest = new IngestService({
      db,
      config,
      secretBox,
      files,
      jobId: context.jobId,
      updateJobProgress: (_jobId, progress) => context.setProgress(progress),
    });
    await ingest.process(documentId);
  });
  worker.register('eval-generate', async (context) => {
    const setId = typeof context.payload.setId === 'string' ? context.payload.setId : '';
    const kbId = typeof context.payload.kbId === 'string' ? context.payload.kbId : '';
    const count = typeof context.payload.count === 'number' ? context.payload.count : 5;
    if (!setId || !kbId) throw new Error('生成评测用例任务参数不完整');
    await evalService.generateCases({ setId, kbId, count }, context.setProgress);
  });
  worker.register('eval-run', async (context) => {
    const runId = typeof context.payload.runId === 'string' ? context.payload.runId : '';
    if (!runId) throw new Error('评测任务缺少 runId');
    await evalService.run(runId, context.setProgress);
  });

  const services: ServerServices = { db, config, secretBox, files, worker, eval: evalService };

  const app = Fastify({
    logger,
    bodyLimit: 21 * 1024 * 1024,
    trustProxy: true,
  });
  app.decorate('adp', services);
  app.decorateRequest('caller', null as never);
  app.decorateRequest('authError', null as never);

  await app.register(cookie);
  await app.register(multipart, {
    limits: {
      fileSize: 20 * 1024 * 1024,
      files: 1,
      fields: 10,
      parts: 12,
    },
  });

  app.addHook('onRequest', async (request) => {
    const result = loadRequestAuth(db, request);
    request.caller = result.caller;
    request.authError = result.error;
  });

  app.addHook('onSend', async (_request, reply) => {
    reply.header('x-content-type-options', 'nosniff');
    reply.header('referrer-policy', 'same-origin');
  });

  app.setErrorHandler((error, request, reply) => {
    if (error instanceof ZodError) {
      const message = error.issues.map((issue) => `${issue.path.join('.') || '输入'}: ${issue.message}`).join('；');
      return reply.status(400).send({ error: 'VALIDATION_ERROR', message });
    }
    if (error instanceof AppError) {
      return reply.status(error.statusCode).send({ error: error.code, message: error.message });
    }
    if (isMultipartLimitError(error as { code?: string; statusCode?: number })) {
      return reply.status(413).send({ error: 'FILE_TOO_LARGE', message: '上传文件超过 20MB 限制' });
    }
    const code = String((error as { code?: string }).code ?? 'INTERNAL_ERROR');
    if (code === 'FST_ERR_CTP_BODY_TOO_LARGE') {
      return reply.status(413).send({ error: 'BODY_TOO_LARGE', message: '请求体过大' });
    }
    if (code === 'FST_ERR_CTP_INVALID_MEDIA_TYPE') {
      return reply.status(415).send({ error: 'UNSUPPORTED_MEDIA_TYPE', message: '不支持的请求内容类型' });
    }
    if (code.startsWith('SQLITE_CONSTRAINT')) {
      const message = code.includes('UNIQUE')
        ? '数据已存在，违反唯一约束'
        : '数据约束校验失败';
      return reply.status(409).send({ error: 'CONFLICT', message });
    }
    request.log.error({ err: error }, 'unhandled request error');
    return reply.status(500).send({ error: 'INTERNAL_ERROR', message: '服务器内部错误' });
  });

  await registerAuthRoutes(app, services);
  await registerOrganizationRoutes(app, services);
  await registerModelRoutes(app, services);
  await registerKnowledgeRoutes(app, services);
  await registerAppRoutes(app, services);
  await registerApiKeyRoutes(app, services);
  await registerTraceRoutes(app, services);
  await registerEvalRoutes(app, services);
  await registerJobRoutes(app, services);

  const distDir = webDistDir ?? path.join(repositoryRoot, 'apps/web/dist');
  const hasWeb = fs.existsSync(path.join(distDir, 'index.html'));
  if (hasWeb) {
    await app.register(fastifyStatic, {
      root: distDir,
      prefix: '/',
      wildcard: false,
      decorateReply: true,
    });
  }

  app.setNotFoundHandler((request, reply) => {
    if (request.url.startsWith('/api/') || request.url === '/health') {
      return reply.status(404).send({ error: 'NOT_FOUND', message: '接口不存在' });
    }
    if (hasWeb && request.method === 'GET') {
      return reply.sendFile('index.html');
    }
    return reply.status(404).send({ error: 'NOT_FOUND', message: '页面不存在' });
  });

  app.addHook('onClose', async () => {
    await worker.stop();
    db.close();
  });

  if (autoStartWorker) worker.start();
  return app;
}

export function redactAppError(error: unknown, config: AppConfig): string {
  return redactSecrets(error instanceof Error ? error.message : String(error), [config.parserToken]);
}
