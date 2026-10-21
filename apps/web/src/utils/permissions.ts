import type { ChunkConfig, RetrievalConfig, DocumentStatus, SourceRef } from '../api/types';

export const DEFAULT_CHUNK_CONFIG: ChunkConfig = {
  strategy: 'structure',
  maxChars: 800,
  overlap: 100,
};

export const DEFAULT_RETRIEVAL_CONFIG: RetrievalConfig = {
  mode: 'hybrid',
  topK: 5,
  threshold: 0,
  rerankModelId: null,
  rewriteModelId: null,
};

export const PERMISSIONS: Array<{ key: string; label: string; group: string; description: string }> = [
  { key: 'knowledge:read', label: '查看知识库', group: '知识库', description: '查看知识与文档、执行检索测试' },
  { key: 'knowledge:write', label: '编辑知识库', group: '知识库', description: '创建知识库、上传与编辑文档和分块' },
  { key: 'knowledge:grant', label: '授权知识库', group: '知识库', description: '为成员分配查看、编辑与授权权限' },
  { key: 'apps:read', label: '使用应用', group: '应用', description: '查看应用与已发布对话' },
  { key: 'apps:write', label: '编辑应用', group: '应用', description: '创建和配置知识问答应用、草稿调试' },
  { key: 'apps:publish', label: '发布应用', group: '应用', description: '发布不可变版本并获取嵌入代码' },
  { key: 'models:manage', label: '管理模型', group: '平台', description: '接入、修改、测试与删除模型' },
  { key: 'eval:manage', label: '管理评测', group: '观测', description: '维护评测集、生成与运行评测' },
  { key: 'observe:read', label: '查看观测', group: '观测', description: '查看链路追踪与终端用户反馈' },
  { key: 'keys:manage', label: '管理 API Key', group: '平台', description: '创建和吊销绑定应用的 API Key' },
  { key: 'system:manage', label: '系统管理', group: '平台', description: '管理组织、用户、角色并查看审计' },
];

export const VISIBILITY_LABELS: Record<string, string> = {
  private: '仅自己',
  organization: '本组织',
  platform: '全平台',
};

export const GRANT_LEVEL_LABELS: Record<string, string> = {
  view: '查看',
  edit: '编辑',
  grant: '授权',
};

export const STRATEGY_LABELS: Record<string, string> = {
  structure: '结构分块',
  semantic: '语义分块',
  'parent-child': '父子分块',
};

export const STRATEGY_DESCRIPTIONS: Record<string, string> = {
  structure: '按标题层级、段落与表格边界切分，保留章节路径，适合制度、手册类文档。',
  semantic: '按语义断点合并相邻段落，减少一句话被截断，适合说明文与长篇文章。',
  'parent-child': '父块保留完整章节用于补全上下文，子块用于精确召回与引用定位。',
};

export const DOCUMENT_STATUS_LABELS: Record<DocumentStatus, string> = {
  QUEUED: '排队中',
  PARSING: '解析中',
  PROCESSING: '向量化',
  READY: '已完成',
  FAILED: '失败',
};

export const RETRIEVAL_MODE_LABELS: Record<string, string> = {
  hybrid: '混合检索',
  keyword: '关键词',
  vector: '语义向量',
};

export const EVAL_STATUS_LABELS: Record<string, string> = {
  QUEUED: '排队中',
  RUNNING: '运行中',
  COMPLETED: '已完成',
  FAILED: '失败',
};

export function isPendingDocument(status: DocumentStatus): boolean {
  return status === 'QUEUED' || status === 'PARSING' || status === 'PROCESSING';
}

export function sourceRefLabel(ref?: SourceRef | null): string {
  if (!ref) return '未标注位置';
  const parts: string[] = [];
  if (ref.page != null) parts.push(`第 ${ref.page} 页`);
  if (ref.sheet) parts.push(`工作表 ${ref.sheet}`);
  if (ref.row != null) parts.push(`第 ${ref.row} 行`);
  if (ref.paragraph != null) parts.push(`第 ${ref.paragraph} 段`);
  if (ref.heading) parts.push(`标题「${ref.heading}」`);
  return parts.length ? parts.join(' · ') : '未标注位置';
}
