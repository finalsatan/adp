import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { afterEach, describe, expect, it } from 'vitest';
import { openDatabase } from '../src/db/index.js';

let dataDir: string | undefined;

afterEach(() => {
  if (dataDir) fs.rmSync(dataDir, { recursive: true, force: true });
  dataDir = undefined;
});

describe('SQLite migrations', () => {
  it('removes the eval_results -> eval_cases cascade while preserving historical rows', () => {
    dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'adp-migration-'));
    const dbPath = path.join(dataDir, 'adp.sqlite');
    const legacy = new DatabaseSync(dbPath);
    legacy.exec('PRAGMA foreign_keys = ON');
    legacy.exec(`
      CREATE TABLE eval_cases (
        id TEXT PRIMARY KEY, set_id TEXT, question TEXT, expected_answer TEXT,
        document_id TEXT, tags TEXT, source TEXT, created_at TEXT
      );
      CREATE TABLE eval_runs (
        id TEXT PRIMARY KEY, set_id TEXT, name TEXT, status TEXT, progress INTEGER,
        config_a TEXT, config_b TEXT, app_snapshot TEXT, metrics_a TEXT, metrics_b TEXT,
        error TEXT, created_at TEXT, started_at TEXT, finished_at TEXT
      );
      CREATE TABLE eval_results (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        case_id TEXT NOT NULL REFERENCES eval_cases(id) ON DELETE CASCADE,
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
      );
      INSERT INTO eval_runs (id, set_id, name, status, progress, config_a, app_snapshot, created_at)
        VALUES ('run-1', 'set-1', '历史运行', 'COMPLETED', 100, '{}', '{}', '2026-01-01T00:00:00.000Z');
      INSERT INTO eval_cases (id, set_id, question, expected_answer, created_at)
        VALUES ('case-1', 'set-1', '问题', '答案', '2026-01-01T00:00:00.000Z');
      INSERT INTO eval_results (id, run_id, case_id, question, expected_answer, answer_a, created_at)
        VALUES ('result-1', 'run-1', 'case-1', '问题', '答案', '回答', '2026-01-01T00:00:00.000Z');
    `);
    legacy.close();

    const db = openDatabase(dataDir);
    try {
      const columns = (db.prepare('PRAGMA table_info(eval_runs)').all() as Array<{ name: string }>).map(
        (row) => row.name,
      );
      expect(columns).toContain('case_snapshot');
      const foreignKeys = db.prepare('PRAGMA foreign_key_list(eval_results)').all() as Array<{ table: string }>;
      expect(foreignKeys.some((row) => row.table === 'eval_cases')).toBe(false);
      const result = db
        .prepare('SELECT case_id, question, expected_answer, answer_a FROM eval_results WHERE id = ?')
        .get('result-1') as { case_id: string; question: string; expected_answer: string; answer_a: string };
      expect(result.case_id).toBe('case-1');
      expect(result.expected_answer).toBe('答案');
      expect(result.answer_a).toBe('回答');
      db.exec("DELETE FROM eval_cases WHERE id = 'case-1'");
      const afterDelete = db.prepare('SELECT COUNT(*) AS n FROM eval_results WHERE id = ?').get('result-1') as {
        n: number;
      };
      expect(afterDelete.n).toBe(1);
      const indexes = db.prepare('PRAGMA index_list(eval_results)').all() as Array<{ name: string; unique: number }>;
      expect(indexes.some((index) => index.name === 'idx_eval_results_run_case' && index.unique === 1)).toBe(true);
    } finally {
      db.close();
    }
  });
});
