import type { FastifyInstance, FastifyReply, FastifyRequest } from 'fastify';
import { z } from 'zod';
import {
  all,
  get,
  run,
  transaction,
  type AppRow,
  type AppVersionRow,
  type ChunkRow,
  type ConversationRow,
  type DocumentRow,
  type KnowledgeBaseRow,
  type MessageRow,
  type ModelRow,
} from '../db/index.js';
import type { ServerServices } from '../context.js';
import { AppError, badRequest, conflict, forbidden, notFound } from '../lib/errors.js';
import { pageOptions, parseBody, parseParams, requireCaller, requireSessionCaller } from '../lib/http.js';
import { appCreateSchema, appPatchSchema, chatSchema, conversationCreateSchema, feedbackSchema, idParams } from '../lib/schemas.js';
import { newId, nowIso, safeJsonParse } from '../lib/util.js';
import { appAccess, assertKbView, knowledgeBaseAccess, permissionsForOrg } from '../services/access.js';
import { writeAudit } from '../services/audit.js';
import { runChat, runChatStream } from '../services/chat.js';
import {
  DEFAULT_RETRIEVAL_CONFIG,
  type RetrievalConfig,
} from '../services/retrieval.js';
import {
  parseVersionConfig,
  serializeApp,
  serializeChunk,
  serializeConversation,
  serializeFeedback,
  serializeMessage,
  type PublishedConfig,
} from '../services/serializers.js';
import { startTrace, finishTrace } from '../services/trace.js';

function appOrThrow(db: ServerServices['db'], id: string): AppRow {
  const app = get<AppRow>(db, 'SELECT * FROM apps WHERE id = ?', id);
  if (!app) throw notFound('应用');
  return app;
}

function currentVersion(db: ServerServices['db'], app: AppRow): AppVersionRow | undefined {
  if (!app.published_version) return undefined;
  return get<AppVersionRow>(
    db,
    'SELECT * FROM app_versions WHERE app_id = ? AND version = ?',
    app.id,
    app.published_version,
  );
}

function publishedSnapshot(db: ServerServices['db'], app: AppRow): PublishedConfig | undefined {
  const version = currentVersion(db, app);
  return version ? parseVersionConfig(version) : undefined;
}

function snapshotVisible(
  caller: ReturnType<typeof requireCaller>,
  app: AppRow,
  snapshot: PublishedConfig,
): boolean {
  if (caller.kind === 'appkey') return caller.appId === app.id;
  if (caller.user.isSystemAdmin) return true;
  if (snapshot.visibility === 'platform') return true;
  if (snapshot.ownerId === caller.user.id) return true;
  if (snapshot.visibility === 'organization') {
    return caller.organizations.some((org) => org.id === snapshot.orgId);
  }
  return false;
}

function requirePublishedSnapshot(
  services: ServerServices,
  caller: ReturnType<typeof requireCaller>,
  app: AppRow,
): PublishedConfig {
  if (app.status !== 'published' || !app.published_version) {
    throw conflict('应用尚未发布');
  }
  const snapshot = publishedSnapshot(services.db, app);
  if (!snapshot) throw conflict('发布版本不存在');
  if (!snapshotVisible(caller, app, snapshot)) throw forbidden('无权访问该发布应用');
  return snapshot;
}

function validateKbsAndModel(
  services: ServerServices,
  caller: ReturnType<typeof requireSessionCaller>,
  kbIds: string[],
  chatModelId: string,
): void {
  for (const kbId of new Set(kbIds)) {
    const kb = get<KnowledgeBaseRow>(services.db, 'SELECT * FROM knowledge_bases WHERE id = ?', kbId);
    if (!kb) throw notFound('知识库');
    if (!knowledgeBaseAccess(services.db, caller, kb).view) throw forbidden('无权使用所选知识库');
  }
  const model = get<ModelRow>(services.db, 'SELECT * FROM models WHERE id = ?', chatModelId);
  if (!model) throw notFound('对话模型');
  if (model.kind !== 'chat' || model.enabled !== 1) throw badRequest('对话模型不可用');
  if (model.scope === 'organization' && model.org_id !== caller.orgId) {
    throw badRequest('不能使用其他组织的对话模型');
  }
}

function validateRetrievalConfig(
  services: ServerServices,
  config: RetrievalConfig,
  orgId: string,
): void {
  const check = (id: string | null | undefined, kind: 'chat' | 'rerank', label: string) => {
    if (!id) return;
    const model = get<ModelRow>(services.db, 'SELECT * FROM models WHERE id = ?', id);
    if (!model) throw badRequest(`${label}模型不存在`);
    if (model.kind !== kind || model.enabled !== 1) throw badRequest(`${label}模型不可用`);
    if (model.scope === 'organization' && model.org_id !== orgId) {
      throw badRequest(`${label}模型不属于当前组织`);
    }
  };
  check(config.rerankModelId, 'rerank', '重排');
  check(config.rewriteModelId, 'chat', '查询改写');
}

function mergedConfig(app: AppRow, patch?: Partial<RetrievalConfig>): RetrievalConfig {
  return {
    ...DEFAULT_RETRIEVAL_CONFIG,
    ...safeJsonParse<Partial<RetrievalConfig>>(app.retrieval_config, {}),
    ...(patch ?? {}),
  };
}

function conversationOrThrow(db: ServerServices['db'], id: string): ConversationRow {
  const conversation = get<ConversationRow>(db, 'SELECT * FROM conversations WHERE id = ?', id);
  if (!conversation) throw notFound('会话');
  return conversation;
}

function checkConversationOwner(
  caller: ReturnType<typeof requireCaller>,
  conversation: ConversationRow,
): void {
  if (conversation.user_id !== caller.user.id) throw notFound('会话');
  if (caller.kind === 'appkey' && conversation.app_id !== caller.appId) throw notFound('会话');
}

function historyFor(db: ServerServices['db'], conversationId: string): Array<{ role: 'user' | 'assistant'; content: string }> {
  const rows = all<MessageRow>(
    db,
    'SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 10',
    conversationId,
  );
  return rows.reverse().map((row) => ({ role: row.role, content: row.content }));
}

function sseWrite(reply: FastifyReply, event: string, data: unknown): void {
  if (reply.raw.writableEnded) return;
  reply.raw.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

export async function registerAppRoutes(app: FastifyInstance, services: ServerServices): Promise<void> {
  const { db, config } = services;

  app.get('/api/apps', async (request) => {
    const caller = requireSessionCaller(request);
    if (!caller.user.isSystemAdmin && !caller.permissions.has('apps:read')) return { items: [] };
    const rows = all<AppRow>(db, 'SELECT * FROM apps ORDER BY created_at DESC');
    const items = rows
      .map((row) => ({ row, access: appAccess(db, caller, row) }))
      .filter((item) => item.access.view)
      .map((item) => serializeApp(db, item.row, item.access));
    return { items };
  });

  app.post('/api/apps', async (request) => {
    const caller = requireSessionCaller(request);
    if (!caller.user.isSystemAdmin && !caller.permissions.has('apps:write')) {
      throw forbidden('缺少 apps:write 权限');
    }
    if (!caller.orgId) throw badRequest('当前没有组织');
    const body = parseBody(request, appCreateSchema);
    const kbIds = [...new Set(body.kbIds)];
    validateKbsAndModel(services, caller, kbIds, body.chatModelId);
    const retrievalConfig = { ...DEFAULT_RETRIEVAL_CONFIG, ...(body.retrievalConfig ?? {}) };
    validateRetrievalConfig(services, retrievalConfig, caller.orgId);
    const id = newId();
    const now = nowIso();
    run(
      db,
      `INSERT INTO apps
       (id, name, description, visibility, org_id, owner_id, kb_ids, chat_model_id, system_prompt,
        retrieval_config, status, published_version, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', NULL, ?, ?)`,
      id,
      body.name,
      body.description,
      body.visibility,
      caller.orgId,
      caller.user.id,
      JSON.stringify(kbIds),
      body.chatModelId,
      body.systemPrompt,
      JSON.stringify(retrievalConfig),
      now,
      now,
    );
    writeAudit(db, {
      orgId: caller.orgId,
      actor: caller.user,
      action: 'app.create',
      resourceType: 'app',
      resourceId: id,
      detail: { name: body.name, kbIds, chatModelId: body.chatModelId },
    });
    return serializeApp(db, appOrThrow(db, id), { view: true, edit: true, publish: true });
  });

  app.get('/api/apps/:id', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const row = appOrThrow(db, id);
    const access = appAccess(db, caller, row);
    if (!access.view) throw notFound('应用');
    return serializeApp(db, row, access);
  });

  app.patch('/api/apps/:id', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const row = appOrThrow(db, id);
    const access = appAccess(db, caller, row);
    if (!access.edit) throw forbidden('无权编辑该应用');
    const body = parseBody(request, appPatchSchema);
    const kbIds = body.kbIds ? [...new Set(body.kbIds)] : safeJsonParse<string[]>(row.kb_ids, []);
    const chatModelId = body.chatModelId ?? row.chat_model_id;
    validateKbsAndModel(services, caller, kbIds, chatModelId);
    const retrievalConfig = mergedConfig(row, body.retrievalConfig);
    validateRetrievalConfig(services, retrievalConfig, row.org_id);
    run(
      db,
      `UPDATE apps SET name = ?, description = ?, visibility = ?, kb_ids = ?, chat_model_id = ?,
         system_prompt = ?, retrieval_config = ?, updated_at = ? WHERE id = ?`,
      body.name ?? row.name,
      body.description ?? row.description,
      body.visibility ?? row.visibility,
      JSON.stringify(kbIds),
      chatModelId,
      body.systemPrompt ?? row.system_prompt,
      JSON.stringify(retrievalConfig),
      nowIso(),
      row.id,
    );
    writeAudit(db, {
      orgId: row.org_id,
      actor: caller.user,
      action: 'app.update',
      resourceType: 'app',
      resourceId: row.id,
      detail: { changed: Object.keys(body), publishedVersion: row.published_version },
    });
    return serializeApp(db, appOrThrow(db, row.id), access);
  });

  app.delete('/api/apps/:id', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const row = appOrThrow(db, id);
    const access = appAccess(db, caller, row);
    if (!access.edit) throw forbidden('无权删除该应用');
    run(db, 'DELETE FROM apps WHERE id = ?', row.id);
    writeAudit(db, {
      orgId: row.org_id,
      actor: caller.user,
      action: 'app.delete',
      resourceType: 'app',
      resourceId: row.id,
      detail: { name: row.name },
    });
    return { ok: true };
  });

  app.post('/api/apps/:id/publish', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const row = appOrThrow(db, id);
    const access = appAccess(db, caller, row);
    if (!access.publish) throw forbidden('缺少发布权限');
    const kbIds = safeJsonParse<string[]>(row.kb_ids, []);
    validateKbsAndModel(services, caller, kbIds, row.chat_model_id);
    const nextVersion = (row.published_version ?? 0) + 1;
    const snapshot: PublishedConfig = {
      name: row.name,
      description: row.description,
      visibility: row.visibility,
      orgId: row.org_id,
      ownerId: row.owner_id,
      kbIds,
      chatModelId: row.chat_model_id,
      systemPrompt: row.system_prompt,
      retrievalConfig: mergedConfig(row),
    };
    transaction(db, () => {
      const existing = get<AppVersionRow>(db, 'SELECT * FROM app_versions WHERE app_id = ? AND version = ?', row.id, nextVersion);
      if (existing) throw conflict('发布版本冲突，请重试');
      run(
        db,
        `INSERT INTO app_versions (id, app_id, version, config, published_by, created_at)
         VALUES (?, ?, ?, ?, ?, ?)`,
        newId(),
        row.id,
        nextVersion,
        JSON.stringify(snapshot),
        caller.user.id,
        nowIso(),
      );
      run(
        db,
        "UPDATE apps SET status = 'published', published_version = ?, updated_at = ? WHERE id = ?",
        nextVersion,
        nowIso(),
        row.id,
      );
    });
    writeAudit(db, {
      orgId: row.org_id,
      actor: caller.user,
      action: 'app.publish',
      resourceType: 'app',
      resourceId: row.id,
      detail: { version: nextVersion, kbIds, chatModelId: row.chat_model_id },
    });
    const base = config.publicBaseUrl?.replace(/\/+$/, '') ?? '';
    const url = `${base}/chat/${row.id}`;
    return {
      version: nextVersion,
      url,
      embedCode: `<iframe src="${base}/chat/${row.id}" title="${row.name.replace(/"/g, '&quot;')}" style="width:100%;height:720px;border:0" loading="lazy"></iframe>`,
    };
  });

  app.get('/api/published/apps/:id', async (request) => {
    const caller = requireCaller(request);
    const { id } = parseParams(request, idParams);
    const row = appOrThrow(db, id);
    const snapshot = requirePublishedSnapshot(services, caller, row);
    const model = get<ModelRow>(db, 'SELECT * FROM models WHERE id = ?', snapshot.chatModelId);
    return {
      id: row.id,
      name: snapshot.name,
      description: snapshot.description,
      version: row.published_version,
      demo: model?.provider === 'demo',
      visibility: snapshot.visibility,
      orgId: snapshot.orgId,
    };
  });

  app.get('/api/apps/:id/conversations', async (request) => {
    const caller = requireCaller(request);
    const { id } = parseParams(request, idParams);
    const row = appOrThrow(db, id);
    const query = request.query as { published?: string };
    if (caller.kind === 'appkey') {
      if (caller.appId !== row.id) throw forbidden('API Key 未绑定该应用');
      requirePublishedSnapshot(services, caller, row);
    } else if (query.published === 'true') {
      requirePublishedSnapshot(services, caller, row);
    } else {
      const access = appAccess(db, caller, row);
      if (!access.view) throw forbidden('无权访问该应用');
    }
    const rows = all<ConversationRow>(
      db,
      'SELECT * FROM conversations WHERE app_id = ? AND user_id = ? ORDER BY updated_at DESC LIMIT 100',
      row.id,
      caller.user.id,
    );
    return { items: rows.map(serializeConversation) };
  });

  app.post('/api/apps/:id/conversations', async (request) => {
    const caller = requireCaller(request);
    const { id } = parseParams(request, idParams);
    const row = appOrThrow(db, id);
    const query = request.query as { published?: string };
    if (caller.kind === 'appkey') {
      if (caller.appId !== row.id) throw forbidden('API Key 未绑定该应用');
      requirePublishedSnapshot(services, caller, row);
    } else if (query.published === 'true') {
      requirePublishedSnapshot(services, caller, row);
    } else {
      const access = appAccess(db, caller, row);
      if (!access.view) throw forbidden('无权访问该应用');
    }
    const body = parseBody(request, conversationCreateSchema);
    const conversationId = newId();
    const now = nowIso();
    run(
      db,
      `INSERT INTO conversations (id, app_id, org_id, user_id, title, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
      conversationId,
      row.id,
      row.org_id,
      caller.user.id,
      body.title?.trim() || '新会话',
      now,
      now,
    );
    return serializeConversation(conversationOrThrow(db, conversationId));
  });

  app.get('/api/conversations/:id/messages', async (request) => {
    const caller = requireCaller(request);
    const { id } = parseParams(request, idParams);
    const conversation = conversationOrThrow(db, id);
    checkConversationOwner(caller, conversation);
    const rows = all<MessageRow>(
      db,
      'SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC, rowid ASC',
      conversation.id,
    );
    return { items: rows.map((row) => serializeMessage(db, row, caller.user.id)) };
  });

  app.post('/api/apps/:id/chat', async (request, reply) => {
    const caller = requireCaller(request);
    const { id } = parseParams(request, idParams);
    const row = appOrThrow(db, id);
    const body = parseBody(request, chatSchema);
    let snapshot: PublishedConfig;
    if (body.draft) {
      if (caller.kind !== 'session') throw forbidden('应用 API Key 不能使用草稿调试');
      const access = appAccess(db, caller, row);
      if (!access.edit) throw forbidden('只有编辑者可以使用草稿调试');
      snapshot = {
        name: row.name,
        description: row.description,
        visibility: row.visibility,
        orgId: row.org_id,
        ownerId: row.owner_id,
        kbIds: safeJsonParse<string[]>(row.kb_ids, []),
        chatModelId: row.chat_model_id,
        systemPrompt: row.system_prompt,
        retrievalConfig: mergedConfig(row),
      };
    } else {
      snapshot = requirePublishedSnapshot(services, caller, row);
    }
    const kbs = all<KnowledgeBaseRow>(
      db,
      `SELECT * FROM knowledge_bases WHERE id IN (${snapshot.kbIds.map(() => '?').join(',') || 'NULL'})`,
      ...snapshot.kbIds,
    );
    if (kbs.length !== snapshot.kbIds.length) throw conflict('发布版本中的知识库已被删除');
    const chatModel = get<ModelRow>(db, 'SELECT * FROM models WHERE id = ?', snapshot.chatModelId);
    if (!chatModel) throw conflict('发布版本中的对话模型已被删除');
    if (chatModel.enabled !== 1) throw conflict('发布版本中的对话模型已禁用');
    let conversation: ConversationRow | undefined;
    if (body.conversationId) {
      conversation = conversationOrThrow(db, body.conversationId);
      checkConversationOwner(caller, conversation);
      if (conversation.app_id !== row.id) throw notFound('会话');
    }
    const newConversation = !conversation;
    if (!conversation) {
      const now = nowIso();
      const convId = newId();
      run(
        db,
        `INSERT INTO conversations (id, app_id, org_id, user_id, title, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?)`,
        convId,
        row.id,
        row.org_id,
        caller.user.id,
        body.message.slice(0, 40),
        now,
        now,
      );
      conversation = conversationOrThrow(db, convId);
    }
    const history = historyFor(db, conversation.id);
    const trace = startTrace(db, {
      kind: 'chat',
      name: `知识问答：${snapshot.name}`,
      orgId: row.org_id,
      userId: caller.user.id,
      appId: row.id,
      input: {
        question: body.message,
        conversationId: conversation.id,
        draft: body.draft,
        publishedVersion: body.draft ? null : row.published_version,
        kbIds: snapshot.kbIds,
        chatModelId: snapshot.chatModelId,
        retrievalConfig: snapshot.retrievalConfig,
      },
    });

    if (body.stream) {
      reply.hijack();
      reply.raw.writeHead(200, {
        'content-type': 'text/event-stream; charset=utf-8',
        'cache-control': 'no-cache, no-transform',
        connection: 'keep-alive',
        'x-accel-buffering': 'no',
      });
      try {
        const output = await runChatStream(
          {
            kbs,
            chatModel,
            systemPrompt: snapshot.systemPrompt,
            question: body.message,
            history,
            retrievalConfig: snapshot.retrievalConfig,
            traceId: trace.id,
            appId: row.id,
          },
          { db, secretBox: services.secretBox, config },
          {
            retrieval: (payload) => {
              sseWrite(reply, 'retrieval', payload);
            },
            delta: (text) => {
              sseWrite(reply, 'delta', { text });
            },
          },
        );
        const saved = transaction(db, () => {
          const userMessage = insertMessage(db, conversation!.id, 'user', body.message, [], null);
          const assistantMessage = insertMessage(
            db,
            conversation!.id,
            'assistant',
            output.answer,
            output.citations,
            trace.id,
          );
          run(
            db,
            'UPDATE conversations SET updated_at = ?, title = CASE WHEN ? THEN ? ELSE title END WHERE id = ?',
            nowIso(),
            newConversation ? 1 : 0,
            body.message.slice(0, 40),
            conversation!.id,
          );
          return { userMessage, assistantMessage };
        });
        sseWrite(reply, 'citations', { citations: output.citations });
        sseWrite(reply, 'done', {
          conversationId: conversation.id,
          message: serializeMessage(db, saved.assistantMessage, caller.user.id),
          userMessage: serializeMessage(db, saved.userMessage, caller.user.id),
          traceId: trace.id,
        });
        finishTrace(db, trace, {
          status: 'OK',
          tokenCount: output.tokenCount || undefined,
          output: {
            answer: output.answer,
            citations: output.citations.length,
            hits: output.hits.length,
            refused: output.refused,
            demo: output.demo,
            rewrittenQuery: output.rewrittenQuery,
          },
        });
        reply.raw.end();
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        finishTrace(db, trace, { status: 'FAILED', output: { error: message } });
        sseWrite(reply, 'error', { error: 'CHAT_FAILED', message });
        reply.raw.end();
      }
      return reply;
    }

    try {
      const output = await runChat(
        {
          kbs,
          chatModel,
          systemPrompt: snapshot.systemPrompt,
          question: body.message,
          history,
          retrievalConfig: snapshot.retrievalConfig,
          traceId: trace.id,
          appId: row.id,
        },
        { db, secretBox: services.secretBox, config },
      );
      const saved = transaction(db, () => {
        const userMessage = insertMessage(db, conversation!.id, 'user', body.message, [], null);
        const assistantMessage = insertMessage(
          db,
          conversation!.id,
          'assistant',
          output.answer,
          output.citations,
          trace.id,
        );
        run(
          db,
          'UPDATE conversations SET updated_at = ?, title = CASE WHEN ? THEN ? ELSE title END WHERE id = ?',
          nowIso(),
          newConversation ? 1 : 0,
          body.message.slice(0, 40),
          conversation!.id,
        );
        return { userMessage, assistantMessage };
      });
      finishTrace(db, trace, {
        status: 'OK',
        tokenCount: output.tokenCount || undefined,
        output: {
          answer: output.answer,
          citations: output.citations.length,
          hits: output.hits.length,
          refused: output.refused,
          demo: output.demo,
          rewrittenQuery: output.rewrittenQuery,
        },
      });
      return {
        conversationId: conversation.id,
        message: serializeMessage(db, saved.assistantMessage, caller.user.id),
        userMessage: serializeMessage(db, saved.userMessage, caller.user.id),
        traceId: trace.id,
        refused: output.refused,
        demo: output.demo,
      };
    } catch (error) {
      finishTrace(db, trace, {
        status: 'FAILED',
        output: { error: error instanceof Error ? error.message : String(error) },
      });
      throw error;
    }
  });

  app.post('/api/messages/:id/feedback', async (request) => {
    const caller = requireCaller(request);
    const { id } = parseParams(request, idParams);
    const message = get<MessageRow>(db, 'SELECT * FROM messages WHERE id = ?', id);
    if (!message) throw notFound('消息');
    if (message.role !== 'assistant') throw badRequest('只能对助手回答进行反馈');
    const conversation = conversationOrThrow(db, message.conversation_id);
    checkConversationOwner(caller, conversation);
    const body = parseBody(request, feedbackSchema);
    const existing = get<{ id: string }>(db, 'SELECT id FROM feedback WHERE message_id = ? AND user_id = ?', message.id, caller.user.id);
    const now = nowIso();
    if (existing) {
      run(
        db,
        'UPDATE feedback SET rating = ?, correction = ?, added_to_eval = 0, updated_at = ? WHERE id = ?',
        body.rating,
        body.correction ?? null,
        now,
        existing.id,
      );
    } else {
      run(
        db,
        `INSERT INTO feedback (id, message_id, user_id, rating, correction, added_to_eval, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, 0, ?, ?)`,
        newId(),
        message.id,
        caller.user.id,
        body.rating,
        body.correction ?? null,
        now,
        now,
      );
    }
    const row = get<Record<string, unknown>>(
      db,
      'SELECT * FROM feedback WHERE message_id = ? AND user_id = ?',
      message.id,
      caller.user.id,
    )!;
    return serializeFeedback(db, row as never);
  });

  app.get('/api/citations/:chunkId', async (request) => {
    const caller = requireCaller(request);
    const params = parseParams(request, z.object({ chunkId: z.string().min(1) }));
    const appId = (request.query as { appId?: string }).appId;
    if (!appId) throw badRequest('缺少 appId');
    const appRow = appOrThrow(db, appId);
    const snapshot = requirePublishedSnapshot(services, caller, appRow);
    const chunk = get<ChunkRow>(db, 'SELECT * FROM chunks WHERE id = ?', params.chunkId);
    if (!chunk) throw notFound('引用分块');
    if (!snapshot.kbIds.includes(chunk.kb_id)) throw forbidden('该分块不属于此发布应用的知识库');
    const document = get<DocumentRow>(db, 'SELECT * FROM documents WHERE id = ?', chunk.document_id);
    return {
      ...serializeChunk(chunk),
      documentName: document?.name ?? '',
      appId: appRow.id,
    };
  });

  app.get('/api/feedback', async (request) => {
    const caller = requireSessionCaller(request);
    if (!caller.user.isSystemAdmin && !caller.permissions.has('observe:read')) {
      throw forbidden('缺少 observe:read 权限');
    }
    const { limit, offset } = pageOptions(request, 100);
    const rows = caller.user.isSystemAdmin
      ? all<Record<string, unknown>>(db, 'SELECT * FROM feedback ORDER BY created_at DESC LIMIT ? OFFSET ?', limit, offset)
      : all<Record<string, unknown>>(
          db,
          `SELECT f.* FROM feedback f
           JOIN messages m ON m.id = f.message_id
           JOIN conversations c ON c.id = m.conversation_id
           WHERE c.org_id = ? ORDER BY f.created_at DESC LIMIT ? OFFSET ?`,
          caller.orgId,
          limit,
          offset,
        );
    return { items: rows.map((row) => serializeFeedback(db, row as never)) };
  });
}

function insertMessage(
  db: ServerServices['db'],
  conversationId: string,
  role: 'user' | 'assistant',
  content: string,
  citations: unknown[],
  traceId: string | null,
): MessageRow {
  const id = newId();
  run(
    db,
    `INSERT INTO messages (id, conversation_id, role, content, citations, trace_id, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
    id,
    conversationId,
    role,
    content,
    JSON.stringify(citations),
    traceId,
    nowIso(),
  );
  return get<MessageRow>(db, 'SELECT * FROM messages WHERE id = ?', id)!;
}
