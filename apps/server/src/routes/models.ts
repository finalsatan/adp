import type { FastifyInstance } from 'fastify';
import { all, get, run, type AppRow, type KnowledgeBaseRow, type ModelRow } from '../db/index.js';
import type { ServerServices } from '../context.js';
import { AppError, conflict, forbidden, notFound } from '../lib/errors.js';
import { parseBody, parseParams, requireSessionCaller } from '../lib/http.js';
import { idParams, modelCreateSchema, modelPatchSchema } from '../lib/schemas.js';
import { newId, nowIso } from '../lib/util.js';
import { canManageModel, permissionsForOrg } from '../services/access.js';
import { writeAudit } from '../services/audit.js';
import { openModelKey, serializeModel } from '../services/modelKeys.js';
import { testModel } from '../services/models.js';

function visibleModels(db: ServerServices['db'], caller: ReturnType<typeof requireSessionCaller>): ModelRow[] {
  if (caller.user.isSystemAdmin) {
    return all<ModelRow>(db, 'SELECT * FROM models ORDER BY created_at ASC');
  }
  return all<ModelRow>(
    db,
    `SELECT * FROM models
     WHERE scope = 'platform' OR (scope = 'organization' AND org_id = ?)
     ORDER BY created_at ASC`,
    caller.orgId,
  );
}

function requireModelManager(caller: ReturnType<typeof requireSessionCaller>, model: ModelRow): void {
  if (!canManageModel(caller, model)) throw forbidden('无权管理该模型');
}

function requireModelsManage(caller: ReturnType<typeof requireSessionCaller>, orgId: string | null): void {
  if (caller.user.isSystemAdmin) return;
  if (!orgId || !permissionsForOrg(caller, orgId).includes('models:manage')) {
    throw forbidden('缺少 models:manage 权限');
  }
}

export async function registerModelRoutes(app: FastifyInstance, services: ServerServices): Promise<void> {
  const { db, secretBox, config } = services;

  app.get('/api/models', async (request) => {
    const caller = requireSessionCaller(request);
    const canSeeConfiguration =
      caller.user.isSystemAdmin ||
      caller.permissions.has('models:manage') ||
      caller.permissions.has('knowledge:read') ||
      caller.permissions.has('apps:write');
    const models = visibleModels(db, caller);
    return {
      items: canSeeConfiguration
        ? models.map((model) => serializeModel(model, secretBox))
        : models.map((model) => ({
            id: model.id,
            name: model.name,
            kind: model.kind,
            provider: model.provider,
            scope: model.scope,
            enabled: model.enabled === 1,
            dimensions: model.dimensions ?? undefined,
            createdAt: model.created_at,
          })),
    };
  });

  app.post('/api/models', async (request) => {
    const caller = requireSessionCaller(request);
    if (
      !caller.user.isSystemAdmin &&
      !caller.organizations.some((org) => org.permissions.includes('models:manage'))
    ) {
      throw forbidden('缺少 models:manage 权限');
    }
    const body = parseBody(request, modelCreateSchema);
    if (body.scope === 'platform') {
      if (!caller.user.isSystemAdmin) throw forbidden('只有系统管理员可以创建全平台模型');
    } else {
      const orgId = body.orgId ?? caller.orgId;
      if (!orgId) throw forbidden('当前没有可用的组织');
      requireModelsManage(caller, orgId);
    }
    const orgId = body.scope === 'platform' ? null : body.orgId ?? caller.orgId;
    if (body.scope === 'organization' && !orgId) throw forbidden('组织模型必须指定 orgId');
    if (body.provider === 'openai' && !body.baseUrl.trim()) {
      throw new AppError(400, 'BAD_REQUEST', 'OpenAI 兼容模型必须配置 baseUrl');
    }
    const id = newId();
    run(
      db,
      `INSERT INTO models
       (id, name, kind, provider, base_url, model, scope, org_id, enabled, api_key_enc, dimensions, created_at, created_by)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      id,
      body.name,
      body.kind,
      body.provider,
      body.baseUrl,
      body.model,
      body.scope,
      orgId,
      body.enabled ? 1 : 0,
      body.apiKey ? secretBox.encrypt(body.apiKey) : null,
      body.dimensions ?? null,
      nowIso(),
      caller.user.id,
    );
    const model = get<ModelRow>(db, 'SELECT * FROM models WHERE id = ?', id)!;
    writeAudit(db, {
      orgId,
      actor: caller.user,
      action: 'model.create',
      resourceType: 'model',
      resourceId: model.id,
      detail: { name: model.name, kind: model.kind, provider: model.provider, hasApiKey: Boolean(body.apiKey) },
    });
    return serializeModel(model, secretBox);
  });

  app.patch('/api/models/:id', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const model = get<ModelRow>(db, 'SELECT * FROM models WHERE id = ?', id);
    if (!model) throw notFound('模型');
    requireModelManager(caller, model);
    const body = parseBody(request, modelPatchSchema);
    const keyUpdate = body.apiKey === undefined || body.apiKey === '' ? model.api_key_enc : secretBox.encrypt(body.apiKey);
    run(
      db,
      `UPDATE models SET name = ?, base_url = ?, model = ?, enabled = ?, api_key_enc = ?, dimensions = ? WHERE id = ?`,
      body.name ?? model.name,
      body.baseUrl ?? model.base_url,
      body.model ?? model.model,
      body.enabled === undefined ? model.enabled : body.enabled ? 1 : 0,
      keyUpdate,
      body.dimensions === undefined ? model.dimensions : body.dimensions,
      model.id,
    );
    const updated = get<ModelRow>(db, 'SELECT * FROM models WHERE id = ?', model.id)!;
    writeAudit(db, {
      orgId: model.org_id,
      actor: caller.user,
      action: 'model.update',
      resourceType: 'model',
      resourceId: model.id,
      detail: { changed: Object.keys(body), apiKeyUpdated: Boolean(body.apiKey) },
    });
    return serializeModel(updated, secretBox);
  });

  app.delete('/api/models/:id', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const model = get<ModelRow>(db, 'SELECT * FROM models WHERE id = ?', id);
    if (!model) throw notFound('模型');
    requireModelManager(caller, model);
    const usedByKb = get<KnowledgeBaseRow>(db, 'SELECT * FROM knowledge_bases WHERE embedding_model_id = ? LIMIT 1', id);
    const usedByApp = get<AppRow>(db, 'SELECT * FROM apps WHERE chat_model_id = ? LIMIT 1', id);
    if (usedByKb || usedByApp) throw conflict('模型正被知识库或应用使用，不能删除');
    const allKbs = all<KnowledgeBaseRow>(db, 'SELECT * FROM knowledge_bases');
    if (
      allKbs.some((kb) => {
        const config = JSON.parse(kb.retrieval_config || '{}') as {
          rerankModelId?: string | null;
          rewriteModelId?: string | null;
        };
        return config.rerankModelId === id || config.rewriteModelId === id;
      })
    ) {
      throw conflict('模型正被知识库检索配置使用，不能删除');
    }
    const allApps = all<AppRow>(db, 'SELECT * FROM apps');
    if (
      allApps.some((app) => {
        const config = JSON.parse(app.retrieval_config || '{}') as {
          rerankModelId?: string | null;
          rewriteModelId?: string | null;
        };
        return config.rerankModelId === id || config.rewriteModelId === id;
      })
    ) {
      throw conflict('模型正被应用检索配置使用，不能删除');
    }
    run(db, 'DELETE FROM models WHERE id = ?', id);
    writeAudit(db, {
      orgId: model.org_id,
      actor: caller.user,
      action: 'model.delete',
      resourceType: 'model',
      resourceId: model.id,
      detail: { name: model.name },
    });
    return { ok: true };
  });

  app.post('/api/models/:id/test', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const model = get<ModelRow>(db, 'SELECT * FROM models WHERE id = ?', id);
    if (!model) throw notFound('模型');
    requireModelManager(caller, model);
    const result = await testModel(model, openModelKey(secretBox, model), {
      timeoutMs: Math.min(config.modelTimeoutMs, 20_000),
    });
    writeAudit(db, {
      orgId: model.org_id,
      actor: caller.user,
      action: 'model.test',
      resourceType: 'model',
      resourceId: model.id,
      detail: { ok: result.ok, latencyMs: result.latencyMs },
    });
    return result;
  });
}
