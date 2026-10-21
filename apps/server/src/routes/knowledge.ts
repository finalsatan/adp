import fs from 'node:fs';
import path from 'node:path';
import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import {
  all,
  get,
  run,
  transaction,
  type AppRow,
  type AppVersionRow,
  type ChunkRow,
  type DocumentRow,
  type KnowledgeBaseRow,
  type ModelRow,
} from '../db/index.js';
import type { ServerServices } from '../context.js';
import { AppError, badRequest, conflict, notFound } from '../lib/errors.js';
import { pageOptions, parseBody, parseParams, requireCaller, requireSessionCaller } from '../lib/http.js';
import {
  chunkConfigPatchSchema,
  chunkMergeSchema,
  chunkPatchSchema,
  chunkSplitSchema,
  documentTextSchema,
  grantSchema,
  idParams,
  kbCreateSchema,
  kbPatchSchema,
  retrievalConfigSchema,
  searchSchema,
  visibilitySchema,
} from '../lib/schemas.js';
import { newId, nowIso, sanitizeFileName, safeJsonParse } from '../lib/util.js';
import { assertKbEdit, assertKbView, knowledgeBaseAccess, permissionsForOrg } from '../services/access.js';
import { writeAudit } from '../services/audit.js';
import type { ChunkConfig } from '../services/chunker.js';
import { embedTexts } from '../services/models.js';
import { openModelKey } from '../services/modelKeys.js';
import {
  DEFAULT_RETRIEVAL_CONFIG,
  retrieveKnowledge,
  type RetrievalConfig,
} from '../services/retrieval.js';
import { findDefaultEmbeddingModel, seedDemoModels } from '../services/seed.js';
import {
  serializeChunk,
  serializeDocument,
  serializeKb,
  parseVersionConfig,
} from '../services/serializers.js';
import { finishTrace, startTrace } from '../services/trace.js';
import { enqueueJob } from '../services/jobs.js';

const MAX_DOCUMENT_BYTES = 20 * 1024 * 1024;
const EXTENSION_MIME: Record<string, { mime: string; kind: 'text' | 'pdf' | 'zip' | 'html' | 'xml' }> = {
  '.txt': { mime: 'text/plain', kind: 'text' },
  '.md': { mime: 'text/markdown', kind: 'text' },
  '.markdown': { mime: 'text/markdown', kind: 'text' },
  '.csv': { mime: 'text/csv', kind: 'text' },
  '.json': { mime: 'application/json', kind: 'text' },
  '.html': { mime: 'text/html', kind: 'html' },
  '.htm': { mime: 'text/html', kind: 'html' },
  '.xml': { mime: 'application/xml', kind: 'xml' },
  '.pdf': { mime: 'application/pdf', kind: 'pdf' },
  '.docx': {
    mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    kind: 'zip',
  },
  '.pptx': {
    mime: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    kind: 'zip',
  },
  '.xlsx': {
    mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    kind: 'zip',
  },
};

const GENERIC_MIMES = new Set(['', 'application/octet-stream', 'binary/octet-stream']);

function normalizeMimeForExtension(ext: string, mime: string): string {
  const expected = EXTENSION_MIME[ext];
  if (!expected) return mime;
  if (!mime || GENERIC_MIMES.has(mime.toLowerCase())) return expected.mime;
  if (ext === '.md' || ext === '.markdown') {
    if (mime === 'text/x-markdown' || mime === 'text/plain') return expected.mime;
  }
  if (ext === '.csv' && mime === 'application/csv') return expected.mime;
  if (ext === '.xml' && mime === 'text/xml') return expected.mime;
  if (ext === '.html' && mime === 'application/xhtml+xml') return expected.mime;
  return mime;
}

function looksBinaryText(buffer: Buffer): boolean {
  const sample = buffer.subarray(0, 8192);
  return sample.includes(0);
}

function assertDocumentType(name: string, declaredMime: string, buffer: Buffer): string {
  const ext = path.extname(name).toLowerCase();
  const expected = EXTENSION_MIME[ext];
  if (!expected) {
    throw badRequest(`不支持的文档扩展名：${ext || '无扩展名'}`, 'UNSUPPORTED_DOCUMENT_TYPE');
  }
  const mime = normalizeMimeForExtension(ext, declaredMime);
  if (mime !== expected.mime) {
    throw badRequest(
      `文件扩展名 ${ext} 与 MIME ${mime || '空'} 不匹配（期望 ${expected.mime}）`,
      'MIME_MISMATCH',
    );
  }
  const pdfMagic = buffer.subarray(0, 5).toString('ascii') === '%PDF-';
  const zipMagic = buffer.length >= 4 && buffer[0] === 0x50 && buffer[1] === 0x4b && buffer[2] === 0x03 && buffer[3] === 0x04;
  if (expected.kind === 'pdf' && !pdfMagic) {
    throw badRequest('PDF 文件签名无效', 'INVALID_FILE_SIGNATURE');
  }
  if (expected.kind === 'zip' && !zipMagic) {
    throw badRequest('Office 文件不是有效的 ZIP 容器', 'INVALID_FILE_SIGNATURE');
  }
  if (expected.kind === 'html' || expected.kind === 'xml') {
    if (looksBinaryText(buffer)) throw badRequest('HTML/XML 文件包含二进制内容', 'INVALID_FILE_SIGNATURE');
  }
  if (expected.kind === 'text') {
    if (looksBinaryText(buffer)) throw badRequest('文本文件包含二进制内容', 'INVALID_FILE_SIGNATURE');
    if (pdfMagic || zipMagic) throw badRequest('文本扩展名不能承载 PDF/ZIP 内容', 'MIME_MISMATCH');
  }
  return mime;
}

function defaultChunkConfig(): ChunkConfig {
  return { strategy: 'structure', maxChars: 800, overlap: 100 };
}

function mergedRetrievalConfig(kb: KnowledgeBaseRow, patch?: Partial<RetrievalConfig>): RetrievalConfig {
  return {
    ...DEFAULT_RETRIEVAL_CONFIG,
    ...safeJsonParse<Partial<RetrievalConfig>>(kb.retrieval_config, {}),
    ...(patch ?? {}),
  };
}

function validateRetrievalModels(
  db: ServerServices['db'],
  caller: ReturnType<typeof requireSessionCaller>,
  config: RetrievalConfig,
  orgId: string,
): void {
  const check = (id: string | null | undefined, kind: 'chat' | 'rerank', label: string) => {
    if (!id) return;
    const model = get<ModelRow>(db, 'SELECT * FROM models WHERE id = ?', id);
    if (!model) throw badRequest(`${label}模型不存在`);
    if (model.kind !== kind) throw badRequest(`${label}模型类型必须是 ${kind}`);
    if (model.enabled !== 1) throw badRequest(`${label}模型已禁用`);
    if (model.scope === 'organization' && model.org_id !== orgId) {
      throw badRequest(`${label}模型不属于当前组织`);
    }
  };
  check(config.rerankModelId, 'rerank', '重排');
  check(config.rewriteModelId, 'chat', '查询改写');
}

function kbOrThrow(db: ServerServices['db'], id: string): KnowledgeBaseRow {
  const kb = get<KnowledgeBaseRow>(db, 'SELECT * FROM knowledge_bases WHERE id = ?', id);
  if (!kb) throw notFound('知识库');
  return kb;
}

function documentOrThrow(db: ServerServices['db'], id: string): DocumentRow {
  const document = get<DocumentRow>(db, 'SELECT * FROM documents WHERE id = ?', id);
  if (!document) throw notFound('文档');
  return document;
}

function kbForDocument(db: ServerServices['db'], document: DocumentRow): KnowledgeBaseRow {
  return kbOrThrow(db, document.kb_id);
}

function chunkOrThrow(db: ServerServices['db'], id: string): ChunkRow {
  const chunk = get<ChunkRow>(db, 'SELECT * FROM chunks WHERE id = ?', id);
  if (!chunk) throw notFound('分块');
  return chunk;
}

function nextPosition(db: ServerServices['db'], documentId: string): number {
  return (get<{ n: number }>(db, 'SELECT COALESCE(MAX(position), -1) + 1 AS n FROM chunks WHERE document_id = ?', documentId)?.n ?? 0);
}

function resolveEmbeddingModel(db: ServerServices['db'], kb: KnowledgeBaseRow): ModelRow {
  const model = kb.embedding_model_id
    ? get<ModelRow>(db, 'SELECT * FROM models WHERE id = ?', kb.embedding_model_id)
    : findDefaultEmbeddingModel(db, kb.org_id);
  if (!model) throw badRequest('知识库未配置 Embedding 模型');
  if (model.kind !== 'embedding') throw badRequest('知识库配置的模型不是 Embedding 模型');
  if (model.enabled !== 1) throw badRequest(`Embedding 模型「${model.name}」已禁用`);
  if (model.scope === 'organization' && model.org_id !== kb.org_id) {
    throw badRequest('知识库不能使用其他组织的 Embedding 模型');
  }
  return model;
}

async function embedForChunk(
  services: ServerServices,
  kb: KnowledgeBaseRow,
  text: string,
): Promise<{ model: ModelRow; vector: number[] }> {
  const model = resolveEmbeddingModel(services.db, kb);
  const result = await embedTexts(model, openModelKey(services.secretBox, model), [text], {
    timeoutMs: services.config.modelTimeoutMs,
  });
  const vector = result.vectors[0];
  if (!vector || vector.length === 0) throw new AppError(502, 'EMBEDDING_ERROR', 'Embedding 服务未返回向量');
  return { model, vector };
}

function updateChunkEmbedding(
  db: ServerServices['db'],
  chunkId: string,
  model: ModelRow,
  vector: number[],
): void {
  run(db, 'DELETE FROM chunk_embeddings WHERE chunk_id = ?', chunkId);
  run(
    db,
    'INSERT INTO chunk_embeddings (chunk_id, model_id, vector, dimensions, created_at) VALUES (?, ?, ?, ?, ?)',
    chunkId,
    model.id,
    JSON.stringify(vector),
    vector.length,
    nowIso(),
  );
}

function enqueueReprocess(services: ServerServices, documentId: string): string {
  run(
    services.db,
    "UPDATE documents SET status = 'QUEUED', progress = 0, error = NULL, updated_at = ? WHERE id = ?",
    nowIso(),
    documentId,
  );
  return enqueueJob(services.db, 'ingest', { documentId });
}

async function readUploadedDocument(
  request: Parameters<typeof parseBody>[0],
  services: ServerServices,
): Promise<{ name: string; mime: string; buffer: Buffer }> {
  if (request.isMultipart()) {
    const part = await request.file({ limits: { fileSize: MAX_DOCUMENT_BYTES, files: 1 } });
    if (!part) throw badRequest('请上传文件');
    let buffer: Buffer;
    try {
      buffer = await part.toBuffer();
    } catch (error) {
      throw new AppError(413, 'FILE_TOO_LARGE', `文件超过 20MB 限制：${error instanceof Error ? error.message : String(error)}`);
    }
    if (part.file.truncated) throw new AppError(413, 'FILE_TOO_LARGE', '文件超过 20MB 限制');
    const name = sanitizeFileName(part.filename || 'document.txt');
    const mime = assertDocumentType(name, part.mimetype || '', buffer);
    if (buffer.length > MAX_DOCUMENT_BYTES) throw new AppError(413, 'FILE_TOO_LARGE', '文件超过 20MB 限制');
    return { name, mime, buffer };
  }
  const body = parseBody(request, documentTextSchema);
  const buffer = Buffer.from(body.content, 'utf8');
  if (buffer.length > MAX_DOCUMENT_BYTES) throw new AppError(413, 'FILE_TOO_LARGE', '内容超过 20MB 限制');
  const name = sanitizeFileName(body.name);
  const mime = assertDocumentType(name, body.mime ?? '', buffer);
  return { name, mime, buffer };
}

export async function registerKnowledgeRoutes(app: FastifyInstance, services: ServerServices): Promise<void> {
  const { db, files } = services;
  seedDemoModels(db);

  app.get('/api/knowledge-bases', async (request) => {
    const caller = requireSessionCaller(request);
    if (!caller.user.isSystemAdmin) {
      const permission = caller.permissions.has('knowledge:read');
      if (!permission) return { items: [] };
    }
    const rows = caller.user.isSystemAdmin
      ? all<KnowledgeBaseRow>(db, 'SELECT * FROM knowledge_bases ORDER BY created_at DESC')
      : all<KnowledgeBaseRow>(db, 'SELECT * FROM knowledge_bases ORDER BY created_at DESC');
    const items = rows
      .map((kb) => ({ kb, access: knowledgeBaseAccess(db, caller, kb) }))
      .filter((item) => item.access.view)
      .map((item) => serializeKb(db, item.kb, item.access));
    return { items };
  });

  app.post('/api/knowledge-bases', async (request) => {
    const caller = requireSessionCaller(request);
    if (!caller.user.isSystemAdmin && !caller.permissions.has('knowledge:write')) {
      throw new AppError(403, 'FORBIDDEN', '缺少 knowledge:write 权限');
    }
    const body = parseBody(request, kbCreateSchema);
    if (!caller.orgId) throw badRequest('当前没有组织，无法创建知识库');
    if (body.visibility === 'platform' && !caller.user.isSystemAdmin && !caller.permissions.has('knowledge:grant')) {
      throw new AppError(403, 'FORBIDDEN', '发布到全平台需要 knowledge:grant 权限');
    }
    const embedding = body.embeddingModelId
      ? get<ModelRow>(db, 'SELECT * FROM models WHERE id = ?', body.embeddingModelId)
      : findDefaultEmbeddingModel(db, caller.orgId);
    if (!embedding) throw badRequest('没有可用的 Embedding 模型，请先配置模型');
    if (embedding.kind !== 'embedding') throw badRequest('embeddingModelId 必须是 Embedding 模型');
    if (embedding.enabled !== 1) throw badRequest('Embedding 模型已禁用');
    if (embedding.scope === 'organization' && embedding.org_id !== caller.orgId) {
      throw badRequest('不能使用其他组织的模型');
    }
    const retrievalConfig = {
      ...DEFAULT_RETRIEVAL_CONFIG,
      ...(body.retrievalConfig ?? {}),
    };
    validateRetrievalModels(db, caller, retrievalConfig, caller.orgId);
    const id = newId();
    const now = nowIso();
    run(
      db,
      `INSERT INTO knowledge_bases
       (id, name, description, visibility, org_id, owner_id, embedding_model_id, chunk_config, retrieval_config, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      id,
      body.name,
      body.description,
      body.visibility,
      caller.orgId,
      caller.user.id,
      embedding.id,
      JSON.stringify({ ...defaultChunkConfig(), ...(body.chunkConfig ?? {}) }),
      JSON.stringify(retrievalConfig),
      now,
      now,
    );
    const kb = kbOrThrow(db, id);
    writeAudit(db, {
      orgId: kb.org_id,
      actor: caller.user,
      action: 'knowledge_base.create',
      resourceType: 'knowledge_base',
      resourceId: kb.id,
      detail: { name: kb.name, visibility: kb.visibility },
    });
    return serializeKb(db, kb, { view: true, edit: true, grant: true });
  });

  app.get('/api/knowledge-bases/:id', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const kb = kbOrThrow(db, id);
    const access = assertKbView(db, caller, kb);
    return serializeKb(db, kb, access);
  });

  app.patch('/api/knowledge-bases/:id', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const kb = kbOrThrow(db, id);
    const access = assertKbEdit(db, caller, kb);
    const body = parseBody(request, kbPatchSchema);
    if (body.visibility === 'platform' && !access.grant) {
      throw new AppError(403, 'FORBIDDEN', '发布到全平台需要授权权限');
    }
    let embeddingModelId = kb.embedding_model_id;
    if (body.embeddingModelId !== undefined) {
      if (body.embeddingModelId === null) {
        const fallback = findDefaultEmbeddingModel(db, kb.org_id);
        embeddingModelId = fallback?.id ?? null;
      } else {
        const model = get<ModelRow>(db, 'SELECT * FROM models WHERE id = ?', body.embeddingModelId);
        if (!model || model.kind !== 'embedding' || model.enabled !== 1) {
          throw badRequest('Embedding 模型不存在、类型错误或已禁用');
        }
        if (model.scope === 'organization' && model.org_id !== kb.org_id) {
          throw badRequest('不能使用其他组织的模型');
        }
        embeddingModelId = model.id;
      }
    }
    const currentChunkConfig = safeJsonParse<ChunkConfig>(kb.chunk_config, defaultChunkConfig());
    const chunkConfig = { ...currentChunkConfig, ...(body.chunkConfig ?? {}) };
    if (chunkConfig.overlap >= chunkConfig.maxChars) throw badRequest('overlap 必须小于 maxChars');
    const retrievalConfig = {
      ...mergedRetrievalConfig(kb, body.retrievalConfig),
    };
    validateRetrievalModels(db, caller, retrievalConfig, kb.org_id);
    if (body.embeddingModelId !== undefined && embeddingModelId !== kb.embedding_model_id) {
      const documentCount = get<{ n: number }>(db, 'SELECT COUNT(*) AS n FROM documents WHERE kb_id = ?', kb.id)?.n ?? 0;
      if (documentCount > 0) {
        throw conflict('知识库已有文档，不能直接更换 Embedding 模型；请新建空知识库或保持原模型');
      }
    }
    const configChanged = body.chunkConfig !== undefined;
    run(
      db,
      `UPDATE knowledge_bases SET name = ?, description = ?, visibility = ?, embedding_model_id = ?,
         chunk_config = ?, retrieval_config = ?, updated_at = ? WHERE id = ?`,
      body.name ?? kb.name,
      body.description ?? kb.description,
      body.visibility ?? kb.visibility,
      embeddingModelId,
      JSON.stringify(chunkConfig),
      JSON.stringify(retrievalConfig),
      nowIso(),
      kb.id,
    );
    writeAudit(db, {
      orgId: kb.org_id,
      actor: caller.user,
      action: 'knowledge_base.update',
      resourceType: 'knowledge_base',
      resourceId: kb.id,
      detail: { changed: Object.keys(body), requiresManualReprocess: configChanged },
    });
    return serializeKb(db, kbOrThrow(db, kb.id), access);
  });

  app.delete('/api/knowledge-bases/:id', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const kb = kbOrThrow(db, id);
    assertKbEdit(db, caller, kb);
    const apps = all<AppRow>(db, 'SELECT * FROM apps');
    if (apps.some((app) => safeJsonParse<string[]>(app.kb_ids, []).includes(kb.id))) {
      throw conflict('知识库仍被应用草稿引用，无法删除');
    }
    const versions = all<AppVersionRow>(db, 'SELECT * FROM app_versions');
    if (versions.some((version) => parseVersionConfig(version).kbIds.includes(kb.id))) {
      throw conflict('知识库仍被已发布应用版本引用，无法删除');
    }
    const docs = all<DocumentRow>(db, 'SELECT * FROM documents WHERE kb_id = ?', kb.id);
    for (const document of docs) files.removeDocument(document.id);
    run(db, 'DELETE FROM knowledge_bases WHERE id = ?', kb.id);
    writeAudit(db, {
      orgId: kb.org_id,
      actor: caller.user,
      action: 'knowledge_base.delete',
      resourceType: 'knowledge_base',
      resourceId: kb.id,
      detail: { name: kb.name, documents: docs.length },
    });
    return { ok: true };
  });

  app.get('/api/knowledge-bases/:id/grants', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const kb = kbOrThrow(db, id);
    const access = assertKbView(db, caller, kb);
    if (!access.grant) throw new AppError(403, 'FORBIDDEN', '缺少授权权限');
    const rows = all<{
      user_id: string;
      level: string;
      name: string;
      email: string;
      created_at: string;
    }>(
      db,
      `SELECT g.user_id, g.level, g.created_at, u.name, u.email
       FROM kb_grants g JOIN users u ON u.id = g.user_id
       WHERE g.kb_id = ? ORDER BY g.created_at`,
      kb.id,
    );
    return {
      items: rows.map((row) => ({
        userId: row.user_id,
        name: row.name,
        email: row.email,
        level: row.level,
        createdAt: row.created_at,
      })),
    };
  });

  app.get('/api/knowledge-bases/:id/grant-candidates', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const kb = kbOrThrow(db, id);
    const access = assertKbView(db, caller, kb);
    if (!access.grant) throw new AppError(403, 'FORBIDDEN', '缺少该知识库的授权权限');
    let rows: Array<{ id: string; name: string; email: string }>;
    if (caller.user.isSystemAdmin) {
      rows = all<{ id: string; name: string; email: string }>(
        db,
        'SELECT id, name, email FROM users ORDER BY name ASC, created_at ASC',
      );
    } else {
      rows = all<{ id: string; name: string; email: string }>(
        db,
        `SELECT DISTINCT u.id, u.name, u.email
         FROM users u
         WHERE u.id = ?
            OR u.id IN (SELECT user_id FROM memberships WHERE org_id = ?)
            OR u.id IN (SELECT user_id FROM kb_grants WHERE kb_id = ?)
         ORDER BY u.name ASC, u.created_at ASC`,
        kb.owner_id,
        kb.org_id,
        kb.id,
      );
    }
    return { items: rows.map((row) => ({ id: row.id, name: row.name, email: row.email })) };
  });

  app.post('/api/knowledge-bases/:id/grants', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const kb = kbOrThrow(db, id);
    const access = assertKbView(db, caller, kb);
    if (!access.grant) throw new AppError(403, 'FORBIDDEN', '缺少授权权限');
    const body = parseBody(request, grantSchema);
    const user = get<{ id: string }>(db, 'SELECT id FROM users WHERE id = ?', body.userId);
    if (!user) throw notFound('用户');
    run(
      db,
      `INSERT INTO kb_grants (kb_id, user_id, level, granted_by, created_at)
       VALUES (?, ?, ?, ?, ?)
       ON CONFLICT(kb_id, user_id) DO UPDATE SET level = excluded.level, granted_by = excluded.granted_by`,
      kb.id,
      body.userId,
      body.level,
      caller.user.id,
      nowIso(),
    );
    writeAudit(db, {
      orgId: kb.org_id,
      actor: caller.user,
      action: 'knowledge_base.grant',
      resourceType: 'knowledge_base',
      resourceId: kb.id,
      detail: { userId: body.userId, level: body.level },
    });
    return { ok: true, userId: body.userId, level: body.level };
  });

  app.delete('/api/knowledge-bases/:id/grants/:userId', async (request) => {
    const caller = requireSessionCaller(request);
    const params = parseParams(request, idParams.extend({ userId: z.string().min(1) }));
    const kb = kbOrThrow(db, params.id);
    const access = assertKbView(db, caller, kb);
    if (!access.grant) throw new AppError(403, 'FORBIDDEN', '缺少授权权限');
    run(db, 'DELETE FROM kb_grants WHERE kb_id = ? AND user_id = ?', kb.id, params.userId);
    writeAudit(db, {
      orgId: kb.org_id,
      actor: caller.user,
      action: 'knowledge_base.revoke',
      resourceType: 'knowledge_base',
      resourceId: kb.id,
      detail: { userId: params.userId },
    });
    return { ok: true };
  });

  app.get('/api/knowledge-bases/:id/documents', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const kb = kbOrThrow(db, id);
    assertKbView(db, caller, kb);
    const rows = all<DocumentRow>(db, 'SELECT * FROM documents WHERE kb_id = ? ORDER BY created_at DESC', kb.id);
    return { items: rows.map((row) => serializeDocument(row)) };
  });

  app.post('/api/knowledge-bases/:id/documents', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const kb = kbOrThrow(db, id);
    assertKbEdit(db, caller, kb);
    const uploaded = await readUploadedDocument(request, services);
    const documentId = newId();
    const relative = files.store(documentId, 1, uploaded.name, uploaded.buffer);
    const now = nowIso();
    run(
      db,
      `INSERT INTO documents
       (id, kb_id, name, mime, size, status, progress, error, chunk_count, version, source_path, parsed_text, parsed_elements, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, 'QUEUED', 0, NULL, 0, 1, ?, NULL, NULL, ?, ?)`,
      documentId,
      kb.id,
      uploaded.name,
      uploaded.mime,
      uploaded.buffer.length,
      relative,
      now,
      now,
    );
    const jobId = enqueueJob(db, 'ingest', { documentId });
    writeAudit(db, {
      orgId: kb.org_id,
      actor: caller.user,
      action: 'document.upload',
      resourceType: 'document',
      resourceId: documentId,
      detail: { kbId: kb.id, name: uploaded.name, size: uploaded.buffer.length },
    });
    return { ...serializeDocument(documentOrThrow(db, documentId)), jobId };
  });

  app.get('/api/documents/:id', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const document = documentOrThrow(db, id);
    const kb = kbForDocument(db, document);
    assertKbView(db, caller, kb);
    return serializeDocument(document, true);
  });

  app.get('/api/documents/:id/source', async (request, reply) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const document = documentOrThrow(db, id);
    const kb = kbForDocument(db, document);
    assertKbView(db, caller, kb);
    const source = files.resolve(document.source_path);
    if (!source || !fs.existsSync(source)) throw notFound('源文件');
    const fileName = encodeURIComponent(document.name);
    const inlineMimes = new Set(['application/pdf', 'text/plain', 'text/markdown', 'text/csv', 'application/json']);
    const disposition = inlineMimes.has(document.mime) ? 'inline' : 'attachment';
    return reply
      .header('content-type', document.mime || 'application/octet-stream')
      .header('content-length', String(fs.statSync(source).size))
      .header('content-disposition', `${disposition}; filename*=UTF-8''${fileName}`)
      .header('x-content-type-options', 'nosniff')
      .header('content-security-policy', "sandbox; default-src 'none'")
      .send(fs.createReadStream(source));
  });

  app.put('/api/documents/:id/file', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const document = documentOrThrow(db, id);
    const kb = kbForDocument(db, document);
    assertKbEdit(db, caller, kb);
    const uploaded = await readUploadedDocument(request, services);
    const version = document.version + 1;
    const relative = files.store(document.id, version, uploaded.name, uploaded.buffer);
    const oldSource = document.source_path;
    run(
      db,
      `UPDATE documents SET name = ?, mime = ?, size = ?, status = 'QUEUED', progress = 0,
         error = NULL, version = ?, source_path = ?, updated_at = ? WHERE id = ?`,
      uploaded.name,
      uploaded.mime,
      uploaded.buffer.length,
      version,
      relative,
      nowIso(),
      document.id,
    );
    files.remove(oldSource);
    const jobId = enqueueJob(db, 'ingest', { documentId: document.id });
    writeAudit(db, {
      orgId: kb.org_id,
      actor: caller.user,
      action: 'document.replace',
      resourceType: 'document',
      resourceId: document.id,
      detail: { version, name: uploaded.name },
    });
    return { ...serializeDocument(documentOrThrow(db, document.id)), jobId };
  });

  app.delete('/api/documents/:id', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const document = documentOrThrow(db, id);
    const kb = kbForDocument(db, document);
    assertKbEdit(db, caller, kb);
    files.removeDocument(document.id);
    run(db, 'DELETE FROM documents WHERE id = ?', document.id);
    writeAudit(db, {
      orgId: kb.org_id,
      actor: caller.user,
      action: 'document.delete',
      resourceType: 'document',
      resourceId: document.id,
      detail: { name: document.name },
    });
    return { ok: true };
  });

  app.post('/api/documents/:id/reprocess', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const document = documentOrThrow(db, id);
    const kb = kbForDocument(db, document);
    assertKbEdit(db, caller, kb);
    const jobId = enqueueReprocess(services, document.id);
    writeAudit(db, {
      orgId: kb.org_id,
      actor: caller.user,
      action: 'document.reprocess',
      resourceType: 'document',
      resourceId: document.id,
    });
    return { ...serializeDocument(documentOrThrow(db, document.id)), jobId };
  });

  app.get('/api/documents/:id/chunks', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const document = documentOrThrow(db, id);
    const kb = kbForDocument(db, document);
    assertKbView(db, caller, kb);
    const rows = all<ChunkRow>(db, 'SELECT * FROM chunks WHERE document_id = ? ORDER BY position ASC', document.id);
    return { items: rows.map(serializeChunk) };
  });

  app.patch('/api/chunks/:id', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const chunk = chunkOrThrow(db, id);
    const document = documentOrThrow(db, chunk.document_id);
    const kb = kbForDocument(db, document);
    assertKbEdit(db, caller, kb);
    const body = parseBody(request, chunkPatchSchema);
    let embedding: { model: ModelRow; vector: number[] } | undefined;
    if (body.content !== undefined && body.content !== chunk.content && chunk.is_parent === 0) {
      embedding = await embedForChunk(services, kb, body.content);
    }
    transaction(db, () => {
      run(
        db,
        'UPDATE chunks SET content = ?, enabled = ?, edited = ?, updated_at = ? WHERE id = ?',
        body.content ?? chunk.content,
        body.enabled === undefined ? chunk.enabled : body.enabled ? 1 : 0,
        body.content !== undefined && body.content !== chunk.content ? 1 : chunk.edited,
        nowIso(),
        chunk.id,
      );
      if (embedding) updateChunkEmbedding(db, chunk.id, embedding.model, embedding.vector);
    });
    writeAudit(db, {
      orgId: kb.org_id,
      actor: caller.user,
      action: 'chunk.update',
      resourceType: 'chunk',
      resourceId: chunk.id,
      detail: { contentChanged: body.content !== undefined, enabled: body.enabled },
    });
    return serializeChunk(chunkOrThrow(db, chunk.id));
  });

  app.post('/api/chunks/:id/split', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const chunk = chunkOrThrow(db, id);
    if (chunk.is_parent === 1) throw badRequest('父级分块不能拆分');
    const document = documentOrThrow(db, chunk.document_id);
    const kb = kbForDocument(db, document);
    assertKbEdit(db, caller, kb);
    const body = parseBody(request, chunkSplitSchema);
    if (body.offset <= 0 || body.offset >= chunk.content.length) {
      throw badRequest('拆分位置必须位于分块内容中间');
    }
    const first = chunk.content.slice(0, body.offset).trim();
    const second = chunk.content.slice(body.offset).trim();
    if (!first || !second) throw badRequest('拆分后的分块不能为空');
    const [embeddingFirst, embeddingSecond] = await Promise.all([
      embedForChunk(services, kb, first),
      embedForChunk(services, kb, second),
    ]);
    const newChunkId = newId();
    const now = nowIso();
    transaction(db, () => {
      run(
        db,
        'UPDATE chunks SET content = ?, edited = 1, updated_at = ? WHERE id = ?',
        first,
        now,
        chunk.id,
      );
      run(
        db,
        'UPDATE chunks SET position = position + 1 WHERE document_id = ? AND position > ?',
        chunk.document_id,
        chunk.position,
      );
      run(
        db,
        `INSERT INTO chunks
         (id, document_id, kb_id, content, heading_path, position, enabled, edited, parent_id, is_parent, source_ref, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, 0, ?, ?, ?)`,
        newChunkId,
        chunk.document_id,
        chunk.kb_id,
        second,
        chunk.heading_path,
        chunk.position + 1,
        chunk.enabled,
        chunk.parent_id,
        chunk.source_ref,
        now,
        now,
      );
      updateChunkEmbedding(db, chunk.id, embeddingFirst.model, embeddingFirst.vector);
      updateChunkEmbedding(db, newChunkId, embeddingSecond.model, embeddingSecond.vector);
      run(
        db,
        'UPDATE documents SET chunk_count = (SELECT COUNT(*) FROM chunks WHERE document_id = ?), updated_at = ? WHERE id = ?',
        chunk.document_id,
        now,
        chunk.document_id,
      );
    });
    writeAudit(db, {
      orgId: kb.org_id,
      actor: caller.user,
      action: 'chunk.split',
      resourceType: 'chunk',
      resourceId: chunk.id,
      detail: { newChunkId, offset: body.offset },
    });
    return { chunk: serializeChunk(chunkOrThrow(db, chunk.id)), newChunk: serializeChunk(chunkOrThrow(db, newChunkId)) };
  });

  app.post('/api/chunks/:id/merge', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const chunk = chunkOrThrow(db, id);
    if (chunk.is_parent === 1) throw badRequest('父级分块不能合并');
    const body = parseBody(request, chunkMergeSchema);
    const next = chunkOrThrow(db, body.nextChunkId);
    if (next.document_id !== chunk.document_id) throw badRequest('只能合并同一文档的分块');
    if (next.is_parent === 1) throw badRequest('父级分块不能合并');
    if (next.position !== chunk.position + 1) throw badRequest('只能合并紧邻的下一个分块');
    const document = documentOrThrow(db, chunk.document_id);
    const kb = kbForDocument(db, document);
    assertKbEdit(db, caller, kb);
    const last = chunk.content.slice(-1);
    const first = next.content.slice(0, 1);
    const wordLike = /[\p{L}\p{N}\u4e00-\u9fff]/u;
    // Preserve an exact split/merge round-trip when the boundary falls inside
    // a word; use a paragraph break only at punctuation/whitespace boundaries.
    const separator = wordLike.test(last) && wordLike.test(first) ? '' : '\n';
    const content = `${chunk.content}${separator}${next.content}`.trim();
    const embedding = await embedForChunk(services, kb, content);
    const now = nowIso();
    transaction(db, () => {
      run(db, 'DELETE FROM chunks WHERE id = ?', next.id);
      run(
        db,
        'UPDATE chunks SET content = ?, edited = 1, enabled = ?, updated_at = ? WHERE id = ?',
        content,
        chunk.enabled === 1 && next.enabled === 1 ? 1 : 0,
        now,
        chunk.id,
      );
      run(
        db,
        'UPDATE chunks SET position = position - 1 WHERE document_id = ? AND position > ?',
        chunk.document_id,
        next.position,
      );
      updateChunkEmbedding(db, chunk.id, embedding.model, embedding.vector);
      run(
        db,
        'UPDATE documents SET chunk_count = (SELECT COUNT(*) FROM chunks WHERE document_id = ?), updated_at = ? WHERE id = ?',
        chunk.document_id,
        now,
        chunk.document_id,
      );
    });
    writeAudit(db, {
      orgId: kb.org_id,
      actor: caller.user,
      action: 'chunk.merge',
      resourceType: 'chunk',
      resourceId: chunk.id,
      detail: { mergedChunkId: next.id },
    });
    return serializeChunk(chunkOrThrow(db, chunk.id));
  });

  app.post('/api/knowledge-bases/:id/search', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const kb = kbOrThrow(db, id);
    assertKbView(db, caller, kb);
    const body = parseBody(request, searchSchema);
    const config = {
      ...mergedRetrievalConfig(kb, body.config as Partial<RetrievalConfig> | undefined),
    };
    validateRetrievalModels(db, caller, config, kb.org_id);
    const trace = startTrace(db, {
      kind: 'search',
      name: `检索测试：${kb.name}`,
      orgId: kb.org_id,
      userId: caller.user.id,
      input: { query: body.query, config },
    });
    try {
      const result = await retrieveKnowledge(
        { db, secretBox: services.secretBox, timeoutMs: services.config.modelTimeoutMs },
        [kb],
        body.query,
        config,
        trace.id,
      );
      finishTrace(db, trace, {
        status: 'OK',
        tokenCount: result.tokenCount || undefined,
        output: {
          hits: result.hits.map((hit) => ({ id: hit.id, documentName: hit.documentName, score: hit.score })),
          rewrittenQuery: result.rewrittenQuery,
        },
      });
      return { items: result.hits, stages: result.stages, traceId: trace.id };
    } catch (error) {
      finishTrace(db, trace, {
        status: 'FAILED',
        output: { error: error instanceof Error ? error.message : String(error) },
      });
      throw error;
    }
  });
}
