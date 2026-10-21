import type {
  ApiKey,
  ApiKeyCreated,
  AppItem,
  AuditItem,
  AuthStatus,
  Branding,
  ChatResponse,
  Chunk,
  Citation,
  CitationDetail,
  Conversation,
  DocumentItem,
  EvalCase,
  EvalRun,
  EvalRunDetail,
  EvalSet,
  EvalReview,
  EvalHumanMetrics,
  FeedbackItem,
  Grant,
  GrantLevel,
  Job,
  KnowledgeBase,
  ListResponse,
  MinimalUser,
  Message,
  Model,
  ModelInput,
  Organization,
  OrganizationMember,
  Overview,
  PublishedApp,
  PublishResult,
  RetrievalConfig,
  Role,
  SearchResponse,
  SessionPayload,
  Trace,
  TraceDetail,
  User,
} from './types';

let activeOrgId: string | null = null;

export function setActiveOrgId(orgId: string | null) {
  activeOrgId = orgId;
}

export function getActiveOrgId() {
  return activeOrgId;
}

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

type RequestOptions = Omit<RequestInit, 'body'> & {
  body?: unknown;
  silentUnauthorized?: boolean;
};

async function parseError(response: Response): Promise<ApiError> {
  let code = `HTTP_${response.status}`;
  let message = `请求失败（${response.status}）`;
  try {
    const text = await response.text();
    if (text) {
      try {
        const data = JSON.parse(text) as { error?: string; message?: string };
        if (data.error) code = data.error;
        if (data.message) message = data.message;
        else if (data.error) message = data.error;
      } catch {
        message = text.slice(0, 400);
      }
    }
  } catch {
    // Keep the generic message when the body cannot be read.
  }
  return new ApiError(response.status, code, message);
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, silentUnauthorized, ...init } = options;
  const headers = new Headers(init.headers);
  if (activeOrgId) headers.set('X-Org-Id', activeOrgId);
  if (body !== undefined && !(body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }

  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      credentials: 'include',
      headers,
      body:
        body === undefined
          ? undefined
          : body instanceof FormData
            ? body
            : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, 'NETWORK_ERROR', '无法连接服务，请确认 API 服务已启动（端口 3001）。');
  }

  if (!response.ok) {
    const error = await parseError(response);
    if (
      response.status === 401 &&
      !silentUnauthorized &&
      !path.startsWith('/auth/')
    ) {
      window.dispatchEvent(new CustomEvent('adp:unauthorized'));
    }
    throw error;
  }

  if (response.status === 204) return undefined as T;
  const text = await response.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    return text as T;
  }
}

const get = <T>(path: string, options?: RequestOptions) =>
  request<T>(path, { ...options, method: 'GET' });
const post = <T>(path: string, body?: unknown, options?: RequestOptions) =>
  request<T>(path, { ...options, method: 'POST', body });
const patch = <T>(path: string, body?: unknown, options?: RequestOptions) =>
  request<T>(path, { ...options, method: 'PATCH', body });
const del = <T>(path: string, options?: RequestOptions) =>
  request<T>(path, { ...options, method: 'DELETE' });

/** Accepts `{items: []}`, a bare array, or a named collection envelope. */
export function unwrapList<T>(payload: ListResponse<T> | Record<string, unknown> | null | undefined, keys: string[] = []): T[] {
  if (!payload) return [];
  if (Array.isArray(payload)) return payload as T[];
  const record = payload as Record<string, unknown>;
  if (Array.isArray(record.items)) return record.items as T[];
  for (const key of keys) {
    if (Array.isArray(record[key])) return record[key] as T[];
  }
  return [];
}

export const authApi = {
  status: () => get<AuthStatus>('/auth/status', { silentUnauthorized: true }),
  branding: () => get<Branding>('/branding', { silentUnauthorized: true }),
  me: () => get<SessionPayload>('/auth/me', { silentUnauthorized: true }),
  setup: (body: { name: string; email: string; password: string; orgName: string }) =>
    post<SessionPayload>('/auth/setup', body, { silentUnauthorized: true }),
  login: (body: { email: string; password: string }) =>
    post<SessionPayload>('/auth/login', body, { silentUnauthorized: true }),
  logout: () => post<void>('/auth/logout', undefined, { silentUnauthorized: true }),
};

export const overviewApi = {
  get: () => get<Overview>('/overview'),
};

export const organizationApi = {
  list: async () => unwrapList<Organization>(await get('/organizations'), ['organizations']),
  create: (body: { name: string; parentId?: string | null }) =>
    post<Organization>('/organizations', body),
  members: async (orgId: string) =>
    unwrapList<OrganizationMember>(await get(`/organizations/${orgId}/members`), ['members']),
  memberCandidates: async (orgId: string) =>
    unwrapList<MinimalUser>(await get(`/organizations/${orgId}/member-candidates`), ['users']),
  roles: async (orgId: string) =>
    unwrapList<Role>(await get(`/organizations/${orgId}/roles`), ['roles']),
  addMember: (orgId: string, body: { userId: string; roleId: string }) =>
    post<void>(`/organizations/${orgId}/members`, body),
  removeMember: (orgId: string, userId: string) =>
    del<void>(`/organizations/${orgId}/members/${userId}`),
};

export const userApi = {
  list: async () => unwrapList<User>(await get('/users'), ['users']),
  create: (body: { name: string; email: string; password: string; orgId: string; roleId?: string }) =>
    post<User>('/users', body),
};

export const roleApi = {
  list: async () => unwrapList<Role>(await get('/roles'), ['roles']),
  create: (body: { name: string; permissions: string[]; global?: boolean; orgId?: string }) =>
    post<Role>('/roles', body),
};

export const auditApi = {
  list: async () => unwrapList<AuditItem>(await get('/audit'), ['audit']),
};

export const modelApi = {
  list: async () => unwrapList<Model>(await get('/models'), ['models']),
  create: (body: ModelInput) => post<Model>('/models', body),
  update: (id: string, body: Partial<ModelInput>) => patch<Model>(`/models/${id}`, body),
  remove: (id: string) => del<void>(`/models/${id}`),
  test: (id: string) =>
    post<{ ok: boolean; message: string; latencyMs: number }>(`/models/${id}/test`),
};

export const knowledgeApi = {
  list: async () => unwrapList<KnowledgeBase>(await get('/knowledge-bases'), ['knowledgeBases']),
  get: (id: string) => get<KnowledgeBase>(`/knowledge-bases/${id}`),
  create: (body: Partial<KnowledgeBase> & { name: string }) =>
    post<KnowledgeBase>('/knowledge-bases', body),
  update: (id: string, body: Partial<KnowledgeBase>) =>
    patch<KnowledgeBase>(`/knowledge-bases/${id}`, body),
  remove: (id: string) => del<void>(`/knowledge-bases/${id}`),
  grants: async (id: string) =>
    unwrapList<Grant>(await get(`/knowledge-bases/${id}/grants`), ['grants']),
  grantCandidates: async (id: string) =>
    unwrapList<MinimalUser>(await get(`/knowledge-bases/${id}/grant-candidates`), ['users']),
  addGrant: (id: string, body: { userId: string; level: GrantLevel }) =>
    post<Grant>(`/knowledge-bases/${id}/grants`, body),
  removeGrant: (id: string, userId: string) =>
    del<void>(`/knowledge-bases/${id}/grants/${userId}`),
  documents: async (id: string) =>
    unwrapList<DocumentItem>(await get(`/knowledge-bases/${id}/documents`), ['documents']),
  uploadDocument: (id: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    return post<DocumentItem>(`/knowledge-bases/${id}/documents`, form);
  },
  createTextDocument: (id: string, body: { name: string; content: string }) =>
    post<DocumentItem>(`/knowledge-bases/${id}/documents`, body),
  search: (id: string, body: { query: string; config?: RetrievalConfig }) =>
    post<SearchResponse>(`/knowledge-bases/${id}/search`, body),
};

export const documentApi = {
  get: (id: string) => get<DocumentItem>(`/documents/${id}`),
  replace: (id: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<DocumentItem>(`/documents/${id}/file`, { method: 'PUT', body: form });
  },
  replaceText: (id: string, body: { name: string; content: string }) =>
    request<DocumentItem>(`/documents/${id}/file`, { method: 'PUT', body }),
  remove: (id: string) => del<void>(`/documents/${id}`),
  reprocess: (id: string) => post<DocumentItem>(`/documents/${id}/reprocess`),
  chunks: async (id: string) => unwrapList<Chunk>(await get(`/documents/${id}/chunks`), ['chunks']),
  updateChunk: (id: string, body: { content?: string; enabled?: boolean }) =>
    patch<Chunk>(`/chunks/${id}`, body),
  splitChunk: (id: string, offset: number) =>
    post<Chunk[]>(`/chunks/${id}/split`, { offset }),
  mergeChunk: (id: string, nextChunkId: string) =>
    post<Chunk>(`/chunks/${id}/merge`, { nextChunkId }),
};

export async function fetchDocumentSource(id: string): Promise<Blob> {
  const headers = new Headers();
  if (activeOrgId) headers.set('X-Org-Id', activeOrgId);
  let response: Response;
  try {
    response = await fetch(`/api/documents/${id}/source`, {
      credentials: 'include',
      headers,
    });
  } catch {
    throw new ApiError(0, 'NETWORK_ERROR', '无法连接服务，请稍后重试。');
  }
  if (!response.ok) throw await parseError(response);
  return response.blob();
}

export const appApi = {
  list: async () => unwrapList<AppItem>(await get('/apps'), ['apps']),
  get: (id: string) => get<AppItem>(`/apps/${id}`),
  create: (body: Partial<AppItem> & { name: string }) => post<AppItem>('/apps', body),
  update: (id: string, body: Partial<AppItem>) => patch<AppItem>(`/apps/${id}`, body),
  remove: (id: string) => del<void>(`/apps/${id}`),
  publish: (id: string) => post<PublishResult>(`/apps/${id}/publish`),
  published: (id: string) => get<PublishedApp>(`/published/apps/${id}`, { silentUnauthorized: true }),
  conversations: async (id: string, published = false) =>
    unwrapList<Conversation>(
      await get(`/apps/${id}/conversations${published ? '?published=true' : ''}`),
      ['conversations'],
    ),
  createConversation: (id: string, body: { title?: string } = {}) =>
    post<Conversation>(`/apps/${id}/conversations`, body),
  messages: async (conversationId: string) =>
    unwrapList<Message>(await get(`/conversations/${conversationId}/messages`), ['messages']),
  feedback: (messageId: string, body: { rating: 1 | -1; correction?: string }) =>
    post<void>(`/messages/${messageId}/feedback`, body),
  citation: (chunkId: string, appId: string) =>
    get<CitationDetail>(`/citations/${chunkId}?appId=${encodeURIComponent(appId)}`, {
      silentUnauthorized: true,
    }),
};

export const feedbackApi = {
  list: async () => unwrapList<FeedbackItem>(await get('/feedback'), ['feedback']),
};

export const apiKeyApi = {
  list: async () => unwrapList<ApiKey>(await get('/api-keys'), ['apiKeys']),
  create: (body: { name: string; appId: string }) => post<ApiKeyCreated>('/api-keys', body),
  revoke: (id: string) => del<void>(`/api-keys/${id}`),
};

export const jobApi = {
  get: (id: string) => get<Job>(`/jobs/${id}`),
};

export const traceApi = {
  list: async () => unwrapList<Trace>(await get('/traces'), ['traces']),
  get: (id: string) => get<TraceDetail>(`/traces/${id}`),
};

export const evalApi = {
  sets: async () => unwrapList<EvalSet>(await get('/eval-sets'), ['evalSets']),
  getSet: (id: string) => get<EvalSet>(`/eval-sets/${id}`),
  createSet: (body: { name: string; description?: string; appId: string }) =>
    post<EvalSet>('/eval-sets', body),
  cases: async (id: string) =>
    unwrapList<EvalCase>(await get(`/eval-sets/${id}/cases`), ['cases']),
  addCase: (
    id: string,
    body:
      | { question: string; expectedAnswer: string; documentId?: string; tags?: string[] }
      | { cases: Array<{ question: string; expectedAnswer: string; documentId?: string; tags?: string[] }> },
  ) => post<EvalCase | EvalCase[]>(`/eval-sets/${id}/cases`, body),
  removeCase: (id: string) => del<void>(`/eval-cases/${id}`),
  generate: (id: string, body: { count: number; kbId: string }) =>
    post<{ jobId: string }>(`/eval-sets/${id}/generate`, body),
  fromFeedback: (id: string, body: { feedbackId: string; expectedAnswer?: string }) =>
    post<EvalCase>(`/eval-sets/${id}/from-feedback`, body),
  runs: async (id: string) => unwrapList<EvalRun>(await get(`/eval-sets/${id}/runs`), ['runs', 'evalRuns']),
  createRun: (
    id: string,
    body: { name?: string; configA: RetrievalConfig; configB?: RetrievalConfig },
  ) => post<EvalRun>(`/eval-sets/${id}/runs`, body),
  run: (id: string) => get<EvalRunDetail>(`/eval-runs/${id}`),
  reviewResult: (
    resultId: string,
    body: { variant: 'A' | 'B'; answerCorrect: boolean; citationsCorrect: boolean; comment?: string },
  ) =>
    patch<{ review: EvalReview; humanMetrics: EvalHumanMetrics }>(
      `/eval-results/${resultId}/review`,
      body,
    ),
};

// ---------------------------------------------------------------------------
// Streaming chat (SSE). The server may also answer with JSON when streaming is
// unavailable; both shapes are handled here.
// ---------------------------------------------------------------------------

export type ChatStreamHandlers = {
  onRetrieval?: (payload: unknown) => void;
  onDelta?: (text: string) => void;
  onCitations?: (citations: Citation[]) => void;
  onDone?: (response: ChatResponse | null) => void;
  onError?: (message: string) => void;
};

function normalizeCitations(payload: unknown): Citation[] {
  if (!payload) return [];
  if (Array.isArray(payload)) return payload as Citation[];
  if (typeof payload === 'object') {
    const record = payload as Record<string, unknown>;
    if (Array.isArray(record.citations)) return record.citations as Citation[];
    if (Array.isArray(record.items)) return record.items as Citation[];
  }
  return [];
}

function normalizeDone(payload: unknown): ChatResponse | null {
  if (!payload || typeof payload !== 'object') return null;
  const record = payload as Record<string, unknown>;
  if (record.message && typeof record.message === 'object') {
    return {
      conversationId: String(record.conversationId ?? ''),
      message: record.message as Message,
      traceId: typeof record.traceId === 'string' ? record.traceId : undefined,
    };
  }
  if (record.data && typeof record.data === 'object') return normalizeDone(record.data);
  return null;
}

export async function streamChat(
  appId: string,
  body: { message: string; conversationId?: string; draft?: boolean },
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const headers = new Headers();
  headers.set('Content-Type', 'application/json');
  headers.set('Accept', 'text/event-stream');
  if (activeOrgId) headers.set('X-Org-Id', activeOrgId);

  let response: Response;
  try {
    response = await fetch(`/api/apps/${appId}/chat`, {
      method: 'POST',
      credentials: 'include',
      headers,
      body: JSON.stringify({ ...body, stream: true }),
      signal,
    });
  } catch (error) {
    if ((error as Error).name === 'AbortError') throw error;
    throw new ApiError(0, 'NETWORK_ERROR', '无法连接服务，请检查网络或 API 服务。');
  }

  if (!response.ok) throw await parseError(response);

  const contentType = response.headers.get('content-type') ?? '';
  if (!contentType.includes('text/event-stream') || !response.body) {
    const text = await response.text();
    let data: ChatResponse | null = null;
    try {
      data = normalizeDone(JSON.parse(text));
    } catch {
      throw new ApiError(0, 'BAD_RESPONSE', '服务返回了无法识别的响应。');
    }
    if (data) handlers.onDone?.(data);
    else throw new ApiError(0, 'BAD_RESPONSE', '服务未返回回答内容。');
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let sawDone = false;

  const handleBlock = (block: string) => {
    const lines = block.split(/\r?\n/);
    let eventName = '';
    const dataLines: string[] = [];
    for (const line of lines) {
      if (line.startsWith('event:')) eventName = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
    }
    if (dataLines.length === 0) return;
    const raw = dataLines.join('\n');
    if (!raw || raw === '[DONE]') return;
    let payload: unknown = raw;
    try {
      payload = JSON.parse(raw);
    } catch {
      // Some servers stream raw text deltas rather than JSON payloads.
    }
    const record = (payload && typeof payload === 'object' ? payload : {}) as Record<string, unknown>;
    const type = eventName || (typeof record.type === 'string' ? record.type : '') || (typeof record.event === 'string' ? record.event : '');

    switch (type) {
      case 'retrieval':
        handlers.onRetrieval?.(record.data ?? record.retrieval ?? payload);
        break;
      case 'delta': {
        const text =
          typeof record.text === 'string'
            ? record.text
            : typeof record.delta === 'string'
              ? record.delta
              : typeof payload === 'string'
                ? payload
                : '';
        if (text) handlers.onDelta?.(text);
        break;
      }
      case 'citations':
        handlers.onCitations?.(normalizeCitations(record.data ?? record.citations ?? payload));
        break;
      case 'done': {
        sawDone = true;
        handlers.onDone?.(normalizeDone(record.data ?? payload));
        break;
      }
      case 'error':
        handlers.onError?.(
          typeof record.message === 'string' ? record.message : '回答生成过程中出现错误。',
        );
        break;
      default:
        if (typeof payload === 'string') handlers.onDelta?.(payload);
        break;
    }
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.search(/\r?\n\r?\n/);
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary);
        const match = buffer.match(/\r?\n\r?\n/);
        buffer = buffer.slice(boundary + (match ? match[0].length : 2));
        handleBlock(block);
        boundary = buffer.search(/\r?\n\r?\n/);
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) handleBlock(buffer.trim());
  } finally {
    reader.releaseLock();
  }

  if (!sawDone) {
    handlers.onDone?.(null);
  }
}
