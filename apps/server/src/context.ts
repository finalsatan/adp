import type { AppConfig } from './config.js';
import type { Database } from './db/index.js';
import type { SecretBox } from './lib/util.js';
import type { DocumentFiles } from './services/ingest.js';
import type { JobWorker } from './services/jobs.js';
import type { EvalService } from './services/evaluator.js';

export interface ServerServices {
  db: Database;
  config: AppConfig;
  secretBox: SecretBox;
  files: DocumentFiles;
  worker: JobWorker;
  eval: EvalService;
}
