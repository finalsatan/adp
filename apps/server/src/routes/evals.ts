import type { FastifyInstance } from 'fastify';
import {
  all,
  get,
  run,
  transaction,
  type AppRow,
  type ConversationRow,
  type EvalCaseRow,
  type EvalReviewRow,
  type EvalRunRow,
  type EvalSetRow,
  type KnowledgeBaseRow,
  type MessageRow,
  type ModelRow,
} from '../db/index.js';
import type { ServerServices } from '../context.js';
import { badRequest, conflict, forbidden, notFound } from '../lib/errors.js';
import { parseBody, parseParams, requireSessionCaller } from '../lib/http.js';
import {
  evalCaseInputSchema,
  evalCasesCreateSchema,
  evalFromFeedbackSchema,
  evalGenerateSchema,
  evalReviewSchema,
  evalRunCreateSchema,
  evalSetCreateSchema,
  idParams,
} from '../lib/schemas.js';
import { newId, nowIso, safeJsonParse } from '../lib/util.js';
import { appAccess, knowledgeBaseAccess, permissionsForOrg } from '../services/access.js';
import { writeAudit } from '../services/audit.js';
import { DEFAULT_RETRIEVAL_CONFIG, type RetrievalConfig } from '../services/retrieval.js';
import {
  computeHumanMetrics,
  serializeEvalCase,
  serializeEvalReview,
  serializeEvalRun,
  serializeEvalSetWithCount,
  type PublishedConfig,
} from '../services/serializers.js';
import { enqueueJob } from '../services/jobs.js';

function evalSetOrThrow(db: ServerServices['db'], id: string): EvalSetRow {
  const row = get<EvalSetRow>(db, 'SELECT * FROM eval_sets WHERE id = ?', id);
  if (!row) throw notFound('评测集');
  return row;
}

function requireEvalManager(caller: ReturnType<typeof requireSessionCaller>, orgId: string): void {
  if (caller.user.isSystemAdmin) return;
  if (!permissionsForOrg(caller, orgId).includes('eval:manage')) {
    throw forbidden('缺少 eval:manage 权限');
  }
}

function setAccess(
  services: ServerServices,
  caller: ReturnType<typeof requireSessionCaller>,
  set: EvalSetRow,
): AppRow {
  requireEvalManager(caller, set.org_id);
  const app = get<AppRow>(services.db, 'SELECT * FROM apps WHERE id = ?', set.app_id);
  if (!app) throw notFound('评测应用');
  if (!appAccess(services.db, caller, app).view) throw forbidden('无权访问该评测应用');
  return app;
}

function validateRetrievalModels(
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

function caseDocumentAllowed(
  services: ServerServices,
  app: AppRow,
  documentId: string | null | undefined,
): string | null {
  if (!documentId) return null;
  const appKbs = safeJsonParse<string[]>(app.kb_ids, []);
  const document = get<{ id: string; kb_id: string }>(
    services.db,
    'SELECT id, kb_id FROM documents WHERE id = ?',
    documentId,
  );
  if (!document) throw notFound('文档');
  if (!appKbs.includes(document.kb_id)) throw badRequest('目标文档不属于该应用关联的知识库');
  return document.id;
}

function insertCase(
  services: ServerServices,
  setId: string,
  input: { question: string; expectedAnswer: string; documentId?: string | null; tags?: string[]; source?: string },
): EvalCaseRow {
  const id = newId();
  run(
    services.db,
    `INSERT INTO eval_cases (id, set_id, question, expected_answer, document_id, tags, source, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    id,
    setId,
    input.question,
    input.expectedAnswer,
    input.documentId ?? null,
    JSON.stringify(input.tags ?? []),
    input.source ?? 'manual',
    nowIso(),
  );
  return get<EvalCaseRow>(services.db, 'SELECT * FROM eval_cases WHERE id = ?', id)!;
}

export async function registerEvalRoutes(app: FastifyInstance, services: ServerServices): Promise<void> {
  const { db } = services;

  app.get('/api/eval-sets', async (request) => {
    const caller = requireSessionCaller(request);
    if (!caller.user.isSystemAdmin && !caller.permissions.has('eval:manage')) {
      throw forbidden('缺少 eval:manage 权限');
    }
    const rows = caller.user.isSystemAdmin
      ? all<EvalSetRow>(db, 'SELECT * FROM eval_sets ORDER BY created_at DESC')
      : all<EvalSetRow>(db, 'SELECT * FROM eval_sets WHERE org_id = ? ORDER BY created_at DESC', caller.orgId);
    return { items: rows.map((row) => serializeEvalSetWithCount(db, row)) };
  });

  app.post('/api/eval-sets', async (request) => {
    const caller = requireSessionCaller(request);
    const body = parseBody(request, evalSetCreateSchema);
    const evalApp = get<AppRow>(db, 'SELECT * FROM apps WHERE id = ?', body.appId);
    if (!evalApp) throw notFound('应用');
    requireEvalManager(caller, evalApp.org_id);
    if (!appAccess(db, caller, evalApp).view) throw forbidden('无权访问该应用');
    const id = newId();
    run(
      db,
      'INSERT INTO eval_sets (id, name, description, app_id, org_id, created_at) VALUES (?, ?, ?, ?, ?, ?)',
      id,
      body.name,
      body.description,
      evalApp.id,
      evalApp.org_id,
      nowIso(),
    );
    writeAudit(db, {
      orgId: evalApp.org_id,
      actor: caller.user,
      action: 'eval_set.create',
      resourceType: 'eval_set',
      resourceId: id,
      detail: { name: body.name, appId: evalApp.id },
    });
    return serializeEvalSetWithCount(db, evalSetOrThrow(db, id));
  });

  app.get('/api/eval-sets/:id', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const set = evalSetOrThrow(db, id);
    setAccess(services, caller, set);
    return serializeEvalSetWithCount(db, set);
  });

  app.get('/api/eval-sets/:id/cases', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const set = evalSetOrThrow(db, id);
    setAccess(services, caller, set);
    const rows = all<EvalCaseRow>(db, 'SELECT * FROM eval_cases WHERE set_id = ? ORDER BY created_at ASC', set.id);
    return { items: rows.map(serializeEvalCase) };
  });

  app.post('/api/eval-sets/:id/cases', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const set = evalSetOrThrow(db, id);
    const evalApp = setAccess(services, caller, set);
    const body = parseBody(request, evalCasesCreateSchema);
    const list = 'cases' in body ? body.cases : [body];
    const created = transaction(db, () =>
      list.map((item) => {
        const documentId = caseDocumentAllowed(services, evalApp, item.documentId);
        return insertCase(services, set.id, {
          question: item.question,
          expectedAnswer: item.expectedAnswer ?? '',
          documentId,
          tags: item.tags ?? [],
          source: 'manual',
        });
      }),
    );
    writeAudit(db, {
      orgId: set.org_id,
      actor: caller.user,
      action: 'eval_case.create',
      resourceType: 'eval_case',
      resourceId: set.id,
      detail: { count: created.length },
    });
    return { items: created.map(serializeEvalCase) };
  });

  app.delete('/api/eval-cases/:id', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const testCase = get<EvalCaseRow>(db, 'SELECT * FROM eval_cases WHERE id = ?', id);
    if (!testCase) throw notFound('评测用例');
    const set = evalSetOrThrow(db, testCase.set_id);
    setAccess(services, caller, set);
    run(db, 'DELETE FROM eval_cases WHERE id = ?', testCase.id);
    writeAudit(db, {
      orgId: set.org_id,
      actor: caller.user,
      action: 'eval_case.delete',
      resourceType: 'eval_case',
      resourceId: testCase.id,
    });
    return { ok: true };
  });

  app.post('/api/eval-sets/:id/generate', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const set = evalSetOrThrow(db, id);
    const evalApp = setAccess(services, caller, set);
    const body = parseBody(request, evalGenerateSchema);
    const kb = get<KnowledgeBaseRow>(db, 'SELECT * FROM knowledge_bases WHERE id = ?', body.kbId);
    if (!kb) throw notFound('知识库');
    if (!safeJsonParse<string[]>(evalApp.kb_ids, []).includes(kb.id)) {
      throw badRequest('知识库未关联到评测集的应用');
    }
    if (!knowledgeBaseAccess(db, caller, kb).view) throw forbidden('无权访问该知识库');
    const model = get<ModelRow>(db, 'SELECT * FROM models WHERE id = ?', evalApp.chat_model_id);
    if (!model || model.provider !== 'openai') {
      throw badRequest('本地演示模型不支持按文档生成评测用例；请配置真实 Chat 模型');
    }
    const jobId = enqueueJob(db, 'eval-generate', {
      setId: set.id,
      kbId: kb.id,
      count: body.count,
    });
    writeAudit(db, {
      orgId: set.org_id,
      actor: caller.user,
      action: 'eval_set.generate',
      resourceType: 'eval_set',
      resourceId: set.id,
      detail: { kbId: kb.id, count: body.count, jobId },
    });
    return { jobId };
  });

  app.post('/api/eval-sets/:id/from-feedback', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const set = evalSetOrThrow(db, id);
    const evalApp = setAccess(services, caller, set);
    const body = parseBody(request, evalFromFeedbackSchema);
    const feedback = get<{ id: string; message_id: string; correction: string | null }>(
      db,
      'SELECT * FROM feedback WHERE id = ?',
      body.feedbackId,
    );
    if (!feedback) throw notFound('反馈');
    const message = get<MessageRow>(db, 'SELECT * FROM messages WHERE id = ?', feedback.message_id);
    if (!message) throw notFound('反馈消息');
    const conversation = get<ConversationRow>(db, 'SELECT * FROM conversations WHERE id = ?', message.conversation_id);
    if (!conversation || conversation.app_id !== evalApp.id) throw badRequest('反馈不属于该评测集的应用');
    const question =
      get<MessageRow>(
        db,
        "SELECT * FROM messages WHERE conversation_id = ? AND role = 'user' AND created_at <= ? ORDER BY created_at DESC LIMIT 1",
        conversation.id,
        message.created_at,
      )?.content ?? '';
    if (!question) throw badRequest('无法找到该反馈对应的用户问题');
    const created = insertCase(services, set.id, {
      question,
      expectedAnswer: body.expectedAnswer ?? feedback.correction ?? '',
      documentId: null,
      tags: ['feedback'],
      source: 'feedback',
    });
    run(db, 'UPDATE feedback SET added_to_eval = 1, updated_at = ? WHERE id = ?', nowIso(), feedback.id);
    writeAudit(db, {
      orgId: set.org_id,
      actor: caller.user,
      action: 'eval_case.from_feedback',
      resourceType: 'eval_case',
      resourceId: created.id,
      detail: { feedbackId: feedback.id },
    });
    return serializeEvalCase(created);
  });

  app.post('/api/eval-sets/:id/runs', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const set = evalSetOrThrow(db, id);
    const evalApp = setAccess(services, caller, set);
    const body = parseBody(request, evalRunCreateSchema);
    validateRetrievalModels(services, body.configA, evalApp.org_id);
    if (body.configB) validateRetrievalModels(services, body.configB, evalApp.org_id);
    const cases = all<EvalCaseRow>(db, 'SELECT * FROM eval_cases WHERE set_id = ? ORDER BY created_at ASC', set.id);
    if (cases.length === 0) throw conflict('评测集没有用例，无法运行');
    const caseSnapshot = cases.map((item) => ({
      id: item.id,
      question: item.question,
      expectedAnswer: item.expected_answer,
      documentId: item.document_id,
      tags: safeJsonParse<string[]>(item.tags, []),
      source: item.source,
    }));
    const kbIds = safeJsonParse<string[]>(evalApp.kb_ids, []);
    const snapshot: PublishedConfig = {
      name: evalApp.name,
      description: evalApp.description,
      visibility: evalApp.visibility,
      orgId: evalApp.org_id,
      ownerId: evalApp.owner_id,
      kbIds,
      chatModelId: evalApp.chat_model_id,
      systemPrompt: evalApp.system_prompt,
      retrievalConfig: {
        ...DEFAULT_RETRIEVAL_CONFIG,
        ...safeJsonParse<Partial<RetrievalConfig>>(evalApp.retrieval_config, {}),
      },
    };
    const runId = newId();
    const runName = body.name?.trim() || `评测 ${new Date().toLocaleString('zh-CN', { hour12: false })}`;
    run(
      db,
      `INSERT INTO eval_runs
       (id, set_id, name, status, progress, config_a, config_b, app_snapshot, case_snapshot,
        metrics_a, metrics_b, error, created_at, started_at, finished_at)
       VALUES (?, ?, ?, 'QUEUED', 0, ?, ?, ?, ?, NULL, NULL, NULL, ?, NULL, NULL)`,
      runId,
      set.id,
      runName,
      JSON.stringify(body.configA),
      body.configB ? JSON.stringify(body.configB) : null,
      JSON.stringify(snapshot),
      JSON.stringify(caseSnapshot),
      nowIso(),
    );
    const jobId = enqueueJob(db, 'eval-run', { runId });
    writeAudit(db, {
      orgId: set.org_id,
      actor: caller.user,
      action: 'eval_run.create',
      resourceType: 'eval_run',
      resourceId: runId,
      detail: { setId: set.id, configA: body.configA, configB: body.configB, jobId },
    });
    return serializeEvalRun(db, get<EvalRunRow>(db, 'SELECT * FROM eval_runs WHERE id = ?', runId)!);
  });

  app.get('/api/eval-sets/:id/runs', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const set = evalSetOrThrow(db, id);
    setAccess(services, caller, set);
    const rows = all<EvalRunRow>(db, 'SELECT * FROM eval_runs WHERE set_id = ? ORDER BY created_at DESC', set.id);
    return { items: rows.map((row) => serializeEvalRun(db, row)) };
  });

  app.get('/api/eval-runs/:id', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const runRow = get<EvalRunRow>(db, 'SELECT * FROM eval_runs WHERE id = ?', id);
    if (!runRow) throw notFound('评测运行');
    const set = evalSetOrThrow(db, runRow.set_id);
    setAccess(services, caller, set);
    return serializeEvalRun(db, runRow, true);
  });

  app.patch('/api/eval-results/:id/review', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    const result = get<{
      id: string;
      run_id: string;
      answer_b: string | null;
    }>(db, 'SELECT id, run_id, answer_b FROM eval_results WHERE id = ?', id);
    if (!result) throw notFound('评测结果');
    const runRow = get<EvalRunRow>(db, 'SELECT * FROM eval_runs WHERE id = ?', result.run_id);
    if (!runRow) throw notFound('评测运行');
    const set = evalSetOrThrow(db, runRow.set_id);
    setAccess(services, caller, set);
    if (runRow.status !== 'COMPLETED') {
      throw conflict('评测运行尚未完成，不能进行人工评审');
    }
    const body = parseBody(request, evalReviewSchema);
    if (body.variant === 'B' && (!runRow.config_b || result.answer_b === null)) {
      throw badRequest('该评测结果没有 B 配置，无法评审 B');
    }
    const previous = get<EvalReviewRow>(
      db,
      'SELECT * FROM eval_result_reviews WHERE result_id = ? AND variant = ?',
      result.id,
      body.variant,
    );
    const now = nowIso();
    run(
      db,
      `INSERT INTO eval_result_reviews
       (id, result_id, variant, answer_correct, citations_correct, comment,
        reviewer_id, reviewer_name, reviewed_at, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(result_id, variant) DO UPDATE SET
         answer_correct = excluded.answer_correct,
         citations_correct = excluded.citations_correct,
         comment = excluded.comment,
         reviewer_id = excluded.reviewer_id,
         reviewer_name = excluded.reviewer_name,
         reviewed_at = excluded.reviewed_at`,
      newId(),
      result.id,
      body.variant,
      body.answerCorrect ? 1 : 0,
      body.citationsCorrect ? 1 : 0,
      body.comment ?? null,
      caller.user.id,
      caller.user.name,
      now,
      now,
    );
    const saved = get<EvalReviewRow>(
      db,
      'SELECT * FROM eval_result_reviews WHERE result_id = ? AND variant = ?',
      result.id,
      body.variant,
    )!;
    writeAudit(db, {
      orgId: set.org_id,
      actor: caller.user,
      action: 'eval_review.upsert',
      resourceType: 'eval_result',
      resourceId: result.id,
      detail: {
        runId: runRow.id,
        variant: body.variant,
        answerCorrect: body.answerCorrect,
        citationsCorrect: body.citationsCorrect,
        previous: previous
          ? {
              answerCorrect: previous.answer_correct === 1,
              citationsCorrect: previous.citations_correct === 1,
              reviewerName: previous.reviewer_name,
              reviewedAt: previous.reviewed_at,
            }
          : null,
      },
    });
    return {
      review: serializeEvalReview(saved),
      humanMetrics: computeHumanMetrics(db, runRow.id, Boolean(runRow.config_b)),
    };
  });
}
