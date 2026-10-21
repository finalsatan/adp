import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { SCHEMA_SQL } from './schema.js';

export type Database = DatabaseSync;

export interface UserRow {
  id: string;
  name: string;
  email: string;
  password_hash: string | null;
  is_system_admin: number;
  oidc_subject: string | null;
  created_at: string;
}

export interface OrganizationRow {
  id: string;
  name: string;
  parent_id: string | null;
  created_at: string;
}

export interface RoleRow {
  id: string;
  name: string;
  permissions: string;
  is_global: number;
  org_id: string | null;
  created_at: string;
}

export interface MembershipRow {
  id: string;
  org_id: string;
  user_id: string;
  role_id: string;
  created_at: string;
}

export interface ModelRow {
  id: string;
  name: string;
  kind: 'chat' | 'embedding' | 'rerank' | 'vision';
  provider: 'openai' | 'demo';
  base_url: string;
  model: string;
  scope: 'organization' | 'platform';
  org_id: string | null;
  enabled: number;
  api_key_enc: string | null;
  dimensions: number | null;
  created_at: string;
  created_by: string | null;
}

export interface KnowledgeBaseRow {
  id: string;
  name: string;
  description: string;
  visibility: 'private' | 'organization' | 'platform';
  org_id: string;
  owner_id: string;
  embedding_model_id: string | null;
  chunk_config: string;
  retrieval_config: string;
  created_at: string;
  updated_at: string;
}

export interface DocumentRow {
  id: string;
  kb_id: string;
  name: string;
  mime: string;
  size: number;
  status: 'QUEUED' | 'PARSING' | 'PROCESSING' | 'READY' | 'FAILED';
  progress: number;
  error: string | null;
  chunk_count: number;
  version: number;
  source_path: string | null;
  parsed_text: string | null;
  parsed_elements: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChunkRow {
  id: string;
  document_id: string;
  kb_id: string;
  content: string;
  heading_path: string;
  position: number;
  enabled: number;
  edited: number;
  parent_id: string | null;
  is_parent: number;
  source_ref: string;
  created_at: string;
  updated_at: string;
}

export interface AppRow {
  id: string;
  name: string;
  description: string;
  visibility: 'private' | 'organization' | 'platform';
  org_id: string;
  owner_id: string;
  kb_ids: string;
  chat_model_id: string;
  system_prompt: string;
  retrieval_config: string;
  status: 'draft' | 'published';
  published_version: number | null;
  created_at: string;
  updated_at: string;
}

export interface AppVersionRow {
  id: string;
  app_id: string;
  version: number;
  config: string;
  published_by: string | null;
  created_at: string;
}

export interface ConversationRow {
  id: string;
  app_id: string;
  org_id: string;
  user_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface MessageRow {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant';
  content: string;
  citations: string;
  trace_id: string | null;
  created_at: string;
}

export interface FeedbackRow {
  id: string;
  message_id: string;
  user_id: string;
  rating: number;
  correction: string | null;
  added_to_eval: number;
  created_at: string;
  updated_at: string;
}

export interface ApiKeyRow {
  id: string;
  name: string;
  prefix: string;
  key_hash: string;
  app_id: string;
  created_by: string | null;
  created_at: string;
  revoked_at: string | null;
}

export interface TraceRow {
  id: string;
  kind: 'ingest' | 'search' | 'chat' | 'eval';
  name: string;
  status: 'OK' | 'FAILED';
  duration_ms: number;
  token_count: number | null;
  app_id: string | null;
  org_id: string | null;
  user_id: string | null;
  input: string | null;
  output: string | null;
  created_at: string;
}

export interface SpanRow {
  id: string;
  trace_id: string;
  name: string;
  kind: string;
  duration_ms: number;
  input: string | null;
  output: string | null;
  status: string;
  position: number;
  created_at: string;
}

export interface EvalSetRow {
  id: string;
  name: string;
  description: string;
  app_id: string;
  org_id: string;
  created_at: string;
}

export interface EvalCaseRow {
  id: string;
  set_id: string;
  question: string;
  expected_answer: string;
  document_id: string | null;
  tags: string;
  source: string;
  created_at: string;
}

export interface EvalRunRow {
  id: string;
  set_id: string;
  name: string;
  status: 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED';
  progress: number;
  config_a: string;
  config_b: string | null;
  app_snapshot: string;
  case_snapshot: string;
  metrics_a: string | null;
  metrics_b: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface EvalReviewRow {
  id: string;
  result_id: string;
  variant: 'A' | 'B';
  answer_correct: number;
  citations_correct: number;
  comment: string | null;
  reviewer_id: string | null;
  reviewer_name: string;
  reviewed_at: string;
  created_at: string;
}

export interface JobRow {
  id: string;
  type: string;
  payload: string;
  status: 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED';
  progress: number;
  attempts: number;
  max_attempts: number;
  error: string | null;
  next_run_at: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

const EVAL_RESULTS_TABLE_SQL = `CREATE TABLE eval_results (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
  case_id TEXT NOT NULL,
  question TEXT NOT NULL,
  expected_answer TEXT NOT NULL,
  answer_a TEXT NOT NULL DEFAULT '',
  answer_b TEXT,
  citations_a TEXT NOT NULL DEFAULT '[]',
  citations_b TEXT NOT NULL DEFAULT '[]',
  scores_a TEXT NOT NULL DEFAULT '{}',
  scores_b TEXT,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
)`;

function tableColumns(db: Database, table: string): string[] {
  return (db.prepare(`PRAGMA table_info(${table})`).all() as Array<{ name: string }>).map((row) => row.name);
}

function migrateEvalSchema(db: Database): void {
  if (!tableColumns(db, 'eval_runs').includes('case_snapshot')) {
    db.exec("ALTER TABLE eval_runs ADD COLUMN case_snapshot TEXT NOT NULL DEFAULT '[]'");
  }
  const foreignKeys = db.prepare('PRAGMA foreign_key_list(eval_results)').all() as Array<{
    table?: string;
  }>;
  const hasCaseCascade = foreignKeys.some((row) => row.table === 'eval_cases');
  if (hasCaseCascade) {
    db.exec('PRAGMA foreign_keys = OFF');
    try {
      db.exec('BEGIN IMMEDIATE');
      db.exec('ALTER TABLE eval_results RENAME TO eval_results_legacy');
      db.exec(EVAL_RESULTS_TABLE_SQL);
      db.exec(`INSERT INTO eval_results
        (id, run_id, case_id, question, expected_answer, answer_a, answer_b,
         citations_a, citations_b, scores_a, scores_b, latency_ms, created_at)
        SELECT id, run_id, case_id, question, expected_answer, answer_a, answer_b,
               citations_a, citations_b, scores_a, scores_b, latency_ms, created_at
        FROM eval_results_legacy`);
      db.exec('DROP TABLE eval_results_legacy');
      db.exec('COMMIT');
    } catch (error) {
      try {
        db.exec('ROLLBACK');
      } catch {
        // ignore rollback failure
      }
      throw error;
    } finally {
      db.exec('PRAGMA foreign_keys = ON');
    }
  }
  // Remove historical duplicates before enforcing run+case idempotency.
  db.exec(`DELETE FROM eval_results
    WHERE rowid NOT IN (
      SELECT MIN(rowid) FROM eval_results GROUP BY run_id, case_id
    )`);
  db.exec('CREATE INDEX IF NOT EXISTS idx_eval_results_run ON eval_results(run_id)');
  db.exec('CREATE UNIQUE INDEX IF NOT EXISTS idx_eval_results_run_case ON eval_results(run_id, case_id)');
}

export function openDatabase(dataDir: string): Database {
  fs.mkdirSync(dataDir, { recursive: true });
  const dbPath = path.join(dataDir, 'adp.sqlite');
  const db = new DatabaseSync(dbPath);
  db.exec('PRAGMA journal_mode = WAL;');
  db.exec('PRAGMA synchronous = NORMAL;');
  db.exec('PRAGMA foreign_keys = ON;');
  db.exec('PRAGMA busy_timeout = 5000;');
  db.exec(SCHEMA_SQL);
  migrateEvalSchema(db);
  return db;
}

const transactionDepth = new WeakMap<Database, number>();

export function transaction<T>(db: Database, fn: () => T): T {
  const depth = transactionDepth.get(db) ?? 0;
  const savepoint = `adp_sp_${depth}`;
  if (depth === 0) {
    db.exec('BEGIN IMMEDIATE');
  } else {
    db.exec(`SAVEPOINT ${savepoint}`);
  }
  transactionDepth.set(db, depth + 1);
  try {
    const result = fn();
    if (depth === 0) {
      db.exec('COMMIT');
    } else {
      db.exec(`RELEASE SAVEPOINT ${savepoint}`);
    }
    return result;
  } catch (error) {
    try {
      if (depth === 0) {
        db.exec('ROLLBACK');
      } else {
        db.exec(`ROLLBACK TO SAVEPOINT ${savepoint}`);
        db.exec(`RELEASE SAVEPOINT ${savepoint}`);
      }
    } catch {
      // ignore rollback failure
    }
    throw error;
  } finally {
    transactionDepth.set(db, depth);
  }
}

export function all<T>(db: Database, sql: string, ...params: unknown[]): T[] {
  return db.prepare(sql).all(...(params as never[])) as T[];
}

export function get<T>(db: Database, sql: string, ...params: unknown[]): T | undefined {
  return db.prepare(sql).get(...(params as never[])) as T | undefined;
}

export function run(db: Database, sql: string, ...params: unknown[]): void {
  db.prepare(sql).run(...(params as never[]));
}

export function count(db: Database, sql: string, ...params: unknown[]): number {
  const row = get<{ n: number }>(db, sql, ...params);
  return row?.n ?? 0;
}
