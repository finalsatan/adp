export interface User {
  id: string;
  name: string;
  email: string;
  isSystemAdmin: boolean;
  organizations?: Membership[];
  memberships?: Membership[];
  orgs?: Membership[];
}

export interface Membership {
  orgId?: string;
  id?: string;
  organizationId?: string;
  name?: string;
  role?: string;
  roleId?: string;
  roleName?: string;
}

export interface Organization {
  id: string;
  name: string;
  parentId?: string | null;
  role: string;
  permissions: string[];
}

export interface SessionPayload {
  user: User;
  organizations: Organization[];
}

export interface AuthStatus {
  initialized: boolean;
  oidcEnabled: boolean;
}

export interface Branding {
  name: string;
  subtitle: string;
}

export type Permission =
  | 'knowledge:read'
  | 'knowledge:write'
  | 'knowledge:grant'
  | 'apps:read'
  | 'apps:write'
  | 'apps:publish'
  | 'models:manage'
  | 'eval:manage'
  | 'observe:read'
  | 'keys:manage'
  | 'system:manage';

export type ModelKind = 'chat' | 'embedding' | 'rerank' | 'vision';
export type ModelProvider = 'openai' | 'demo';

export interface Model {
  id: string;
  name: string;
  kind: ModelKind;
  provider: ModelProvider;
  baseUrl: string;
  model: string;
  scope: 'organization' | 'platform';
  enabled: boolean;
  apiKeyMasked?: string | null;
  dimensions?: number | null;
  createdAt: string;
}

export interface ModelInput {
  name: string;
  kind: ModelKind;
  provider: ModelProvider;
  baseUrl: string;
  model: string;
  scope: 'organization' | 'platform';
  enabled: boolean;
  apiKey?: string;
  dimensions?: number | null;
}

export type ChunkStrategy = 'structure' | 'semantic' | 'parent-child';

export interface ChunkConfig {
  strategy: ChunkStrategy;
  maxChars: number;
  overlap: number;
}

export type RetrievalMode = 'hybrid' | 'keyword' | 'vector';

export interface RetrievalConfig {
  mode: RetrievalMode;
  topK: number;
  threshold: number;
  rerankModelId?: string | null;
  rewriteModelId?: string | null;
}

export interface ResourceAccess {
  view: boolean;
  edit: boolean;
  grant: boolean;
}

export interface KnowledgeBase {
  id: string;
  name: string;
  description: string;
  visibility: 'private' | 'organization' | 'platform';
  orgId: string;
  ownerId: string;
  embeddingModelId?: string | null;
  documentCount: number;
  chunkCount: number;
  chunkConfig: ChunkConfig;
  retrievalConfig: RetrievalConfig;
  /** Resource-level access returned by the server; authoritative over menu permissions. */
  access?: ResourceAccess | null;
  createdAt: string;
}

export type DocumentStatus = 'QUEUED' | 'PARSING' | 'PROCESSING' | 'READY' | 'FAILED';

export interface SourceRef {
  page?: number | null;
  sheet?: string | null;
  row?: number | null;
  paragraph?: number | null;
  heading?: string | null;
}

export interface DocumentItem {
  id: string;
  kbId: string;
  name: string;
  mime: string;
  size: number;
  status: DocumentStatus;
  progress: number;
  error?: string | null;
  chunkCount: number;
  version: number;
  createdAt: string;
  parsedText?: string;
  updatedAt?: string;
}

export interface Chunk {
  id: string;
  documentId: string;
  content: string;
  headingPath: string[];
  position: number;
  enabled: boolean;
  edited: boolean;
  parentId?: string | null;
  isParent?: boolean;
  sourceRef: SourceRef;
}

export interface Hit extends Chunk {
  score: number;
  keywordScore?: number;
  vectorScore?: number;
  rerankScore?: number;
  documentName: string;
  kbId: string;
}

export interface SearchStage {
  name: string;
  durationMs: number;
  items: Hit[];
}

export interface SearchResponse {
  items: Hit[];
  stages: SearchStage[];
  traceId: string;
}

export type GrantLevel = 'view' | 'edit' | 'grant';

export interface Grant {
  userId: string;
  userName?: string;
  name?: string;
  userEmail?: string;
  email?: string;
  level: GrantLevel;
  createdAt?: string;
}

export interface AppAccess {
  view: boolean;
  edit: boolean;
  publish: boolean;
}

export interface AppItem {
  id: string;
  name: string;
  description: string;
  visibility: 'private' | 'organization' | 'platform';
  orgId: string;
  kbIds: string[];
  chatModelId: string;
  systemPrompt: string;
  retrievalConfig: RetrievalConfig;
  status: 'draft' | 'published';
  publishedVersion?: number | null;
  access?: AppAccess | null;
  createdAt: string;
}

export interface PublishedApp {
  id: string;
  name: string;
  description: string;
  version: number;
  demo: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
}

export type CitationSourceRef = SourceRef;

export interface Citation {
  index: number;
  chunkId: string;
  documentId: string;
  documentName: string;
  content: string;
  sourceRef: CitationSourceRef;
  headingPath: string[];
}

export interface MessageFeedback {
  rating: 1 | -1;
  correction?: string;
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  citations: Citation[];
  traceId?: string | null;
  feedback?: MessageFeedback | null;
  createdAt: string;
}

export interface ChatResponse {
  conversationId: string;
  message: Message;
  traceId?: string;
}

export interface FeedbackItem {
  id: string;
  messageId: string;
  question: string;
  answer: string;
  rating: 1 | -1;
  correction?: string | null;
  appId: string;
  createdAt: string;
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  appId: string;
  createdAt: string;
  revokedAt?: string | null;
}

export interface ApiKeyCreated {
  id: string;
  key: string;
  prefix: string;
}

export type TraceKind = 'ingest' | 'search' | 'chat' | 'eval';
export type TraceStatus = 'ok' | 'error' | 'running' | 'success' | 'failed' | string;

export interface Trace {
  id: string;
  kind: TraceKind;
  name: string;
  status: TraceStatus;
  durationMs: number;
  createdAt: string;
  tokenCount?: number | null;
  appId?: string | null;
}

export interface Span {
  id: string;
  name: string;
  kind: string;
  durationMs: number;
  input: unknown;
  output: unknown;
  status: TraceStatus;
}

export interface TraceDetail extends Trace {
  spans: Span[];
}

export interface EvalSet {
  id: string;
  name: string;
  description: string;
  appId: string;
  caseCount: number;
  createdAt: string;
}

export interface EvalCase {
  id: string;
  question: string;
  expectedAnswer: string;
  documentId?: string | null;
  tags?: string[];
  createdAt?: string;
}

export interface EvalMetrics {
  total: number;
  /** Fraction 0..1, or null when the reference answer was not annotated. */
  answerMatch: number | null;
  /** Fraction 0..1, or null when citations could not be evaluated. */
  citationAccuracy: number | null;
  /** Fraction 0..1, or null when recall was not annotated. */
  retrievalRecall: number | null;
  /** Fraction 0..1, or null when the run did not classify refusals. */
  refusalRate: number | null;
  avgLatencyMs: number | null;
}

export type EvalRunStatus = 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED';

export interface Job {
  id: string;
  type: string;
  status: EvalRunStatus;
  progress: number;
  error?: string | null;
  attempts: number;
  createdAt: string;
}

export interface EvalHumanMetrics {
  A?: HumanMetrics;
  B?: HumanMetrics;
}

export interface EvalRun {
  id: string;
  setId: string;
  name: string;
  status: EvalRunStatus;
  progress: number;
  configA: RetrievalConfig;
  configB?: RetrievalConfig | null;
  metricsA?: EvalMetrics | null;
  metricsB?: EvalMetrics | null;
  humanMetrics?: EvalHumanMetrics | null;
  error?: string | null;
  createdAt: string;
}

export interface EvalReview {
  answerCorrect?: boolean | null;
  citationsCorrect?: boolean | null;
  comment?: string | null;
  reviewerId?: string | null;
  reviewerName?: string | null;
  reviewedAt?: string | null;
}

export interface HumanMetrics {
  reviewed: number;
  total: number;
  answerAccuracy: number | null;
  citationAccuracy: number | null;
}

export interface EvalScores {
  answerMatch?: number;
  citationAccuracy?: number;
  retrievalRecall?: number;
  refusal?: boolean | number;
  latencyMs?: number;
  [key: string]: unknown;
}

export interface EvalResult {
  id?: string;
  caseId: string;
  reviews?: {
    A?: EvalReview | null;
    B?: EvalReview | null;
  } | null;
  question: string;
  expectedAnswer: string;
  answerA: string;
  answerB?: string | null;
  citationsA?: Citation[];
  citationsB?: Citation[];
  scoresA?: EvalScores | null;
  scoresB?: EvalScores | null;
}

export interface EvalRunDetail extends EvalRun {
  results: EvalResult[];
}

export interface Role {
  id: string;
  name: string;
  permissions: string[];
  global: boolean;
  orgId?: string | null;
}

export interface MinimalUser {
  id: string;
  name: string;
  email: string;
}

export interface OrganizationMember {
  id: string;
  name: string;
  email: string;
  roleId: string;
  roleName: string;
}

export interface AuditItem {
  id: string;
  actorName: string;
  action: string;
  resourceType: string;
  resourceId: string;
  createdAt: string;
  detail?: unknown;
}

export interface Overview {
  knowledgeBases: number;
  documents: number;
  chunks: number;
  apps: number;
  queries: number;
  feedbackPending: number;
  recentTraces: Trace[];
}

export interface PublishResult {
  version: number;
  url: string;
  embedCode: string;
}

export interface CitationDetail {
  chunkId?: string;
  documentId?: string;
  documentName?: string;
  content?: string;
  sourceRef?: SourceRef;
  headingPath?: string[];
  kbId?: string;
  [key: string]: unknown;
}

export type ListResponse<T> = { items: T[] } | T[];
