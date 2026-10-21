import type { FastifyInstance } from 'fastify';
import {
  get,
  type AppRow,
  type Database,
  type EvalRunRow,
  type EvalSetRow,
  type JobRow,
  type KnowledgeBaseRow,
  type ModelRow,
} from '../db/index.js';
import type { ServerServices } from '../context.js';
import { forbidden, notFound } from '../lib/errors.js';
import { parseParams, requireSessionCaller } from '../lib/http.js';
import { idParams } from '../lib/schemas.js';
import { safeJsonParse, redactSecrets, type SecretBox } from '../lib/util.js';
import { appAccess, knowledgeBaseAccess, permissionsForOrg } from '../services/access.js';
import { openModelKey } from '../services/modelKeys.js';
import type { RetrievalConfig } from '../services/retrieval.js';

function addModelSecret(
  secrets: string[],
  db: Database,
  secretBox: SecretBox,
  modelId: string | null | undefined,
): void {
  if (!modelId) return;
  const model = get<ModelRow>(db, 'SELECT * FROM models WHERE id = ?', modelId);
  if (!model) return;
  try {
    const key = openModelKey(secretBox, model);
    if (key) secrets.push(key);
  } catch {
    // A broken key cannot be used for redaction; do not expose the ciphertext.
  }
}

function collectSecrets(
  services: ServerServices,
  payload: Record<string, unknown>,
): string[] {
  const secrets: string[] = [];
  if (services.config.parserToken) secrets.push(services.config.parserToken);
  const addKbModels = (kb: KnowledgeBaseRow) => {
    addModelSecret(secrets, services.db, services.secretBox, kb.embedding_model_id);
    const retrieval = safeJsonParse<Partial<RetrievalConfig>>(kb.retrieval_config, {});
    addModelSecret(secrets, services.db, services.secretBox, retrieval.rerankModelId);
    addModelSecret(secrets, services.db, services.secretBox, retrieval.rewriteModelId);
  };
  if (typeof payload.documentId === 'string') {
    const document = get<{ kb_id: string }>(services.db, 'SELECT kb_id FROM documents WHERE id = ?', payload.documentId);
    if (document) {
      const kb = get<KnowledgeBaseRow>(services.db, 'SELECT * FROM knowledge_bases WHERE id = ?', document.kb_id);
      if (kb) addKbModels(kb);
    }
  }
  const addSetModels = (set: EvalSetRow) => {
    const app = get<AppRow>(services.db, 'SELECT * FROM apps WHERE id = ?', set.app_id);
    if (!app) return;
    addModelSecret(secrets, services.db, services.secretBox, app.chat_model_id);
    const retrieval = safeJsonParse<Partial<RetrievalConfig>>(app.retrieval_config, {});
    addModelSecret(secrets, services.db, services.secretBox, retrieval.rerankModelId);
    addModelSecret(secrets, services.db, services.secretBox, retrieval.rewriteModelId);
    for (const kbId of safeJsonParse<string[]>(app.kb_ids, [])) {
      const kb = get<KnowledgeBaseRow>(services.db, 'SELECT * FROM knowledge_bases WHERE id = ?', kbId);
      if (kb) addKbModels(kb);
    }
  };
  if (typeof payload.setId === 'string') {
    const set = get<EvalSetRow>(services.db, 'SELECT * FROM eval_sets WHERE id = ?', payload.setId);
    if (set) addSetModels(set);
  }
  if (typeof payload.runId === 'string') {
    const run = get<EvalRunRow>(services.db, 'SELECT * FROM eval_runs WHERE id = ?', payload.runId);
    if (run) {
      const set = get<EvalSetRow>(services.db, 'SELECT * FROM eval_sets WHERE id = ?', run.set_id);
      if (set) addSetModels(set);
    }
  }
  return secrets.filter((secret) => secret.length >= 4);
}

function authorizeJob(
  services: ServerServices,
  caller: ReturnType<typeof requireSessionCaller>,
  job: JobRow,
  payload: Record<string, unknown>,
): void {
  if (caller.user.isSystemAdmin) return;
  if (job.type === 'ingest') {
    const documentId = typeof payload.documentId === 'string' ? payload.documentId : '';
    const document = get<{ kb_id: string }>(services.db, 'SELECT kb_id FROM documents WHERE id = ?', documentId);
    if (!document) throw notFound('任务');
    const kb = get<KnowledgeBaseRow>(services.db, 'SELECT * FROM knowledge_bases WHERE id = ?', document.kb_id);
    if (!kb || !knowledgeBaseAccess(services.db, caller, kb).view) throw forbidden('无权查看该任务');
    return;
  }
  if (job.type === 'eval-generate' || job.type === 'eval-run') {
    let set: EvalSetRow | undefined;
    if (job.type === 'eval-generate') {
      const setId = typeof payload.setId === 'string' ? payload.setId : '';
      set = get<EvalSetRow>(services.db, 'SELECT * FROM eval_sets WHERE id = ?', setId);
    } else {
      const runId = typeof payload.runId === 'string' ? payload.runId : '';
      const run = get<EvalRunRow>(services.db, 'SELECT * FROM eval_runs WHERE id = ?', runId);
      set = run
        ? get<EvalSetRow>(services.db, 'SELECT * FROM eval_sets WHERE id = ?', run.set_id)
        : undefined;
    }
    if (!set) throw notFound('任务');
    if (!permissionsForOrg(caller, set.org_id).includes('eval:manage')) {
      throw forbidden('无权查看该任务');
    }
    const evalApp = get<AppRow>(services.db, 'SELECT * FROM apps WHERE id = ?', set.app_id);
    if (!evalApp || !appAccess(services.db, caller, evalApp).view) throw forbidden('无权查看该任务');
    return;
  }
  throw forbidden('无权查看该任务');
}

export async function registerJobRoutes(app: FastifyInstance, services: ServerServices): Promise<void> {
  app.get('/api/jobs/:id', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const job = get<JobRow>(services.db, 'SELECT * FROM jobs WHERE id = ?', id);
    if (!job) throw notFound('任务');
    const payload = safeJsonParse<Record<string, unknown>>(job.payload, {});
    authorizeJob(services, caller, job, payload);
    return {
      id: job.id,
      type: job.type,
      status: job.status,
      progress: job.progress,
      error: job.error ? redactSecrets(job.error, collectSecrets(services, payload)) : null,
      attempts: job.attempts,
      createdAt: job.created_at,
    };
  });
}
