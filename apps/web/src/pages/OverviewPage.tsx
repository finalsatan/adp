import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import {
  Activity,
  ArrowRight,
  BookOpen,
  Bot,
  CircleHelp,
  Database,
  FileText,
  FlaskConical,
  Layers,
  MessageSquareWarning,
  Plus,
} from 'lucide-react';
import { overviewApi } from '../api/client';
import { useApi, useDocumentTitle } from '../hooks';
import { useAuth } from '../contexts/AuthContext';
import {
  Badge,
  Callout,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  SkeletonRows,
  StatusBadge,
} from '../components/ui';
import { formatDateTime, formatDuration, formatNumber } from '../utils/format';

export function OverviewPage() {
  useDocumentTitle('概览');
  const { can, user, currentOrg } = useAuth();
  const overview = useApi(() => overviewApi.get(), []);

  if (overview.error) {
    return (
      <div className="page">
        <PageHeader title="概览" description="当前组织的数据与最近调用。" />
        <ErrorState error={overview.error} onRetry={overview.reload} />
      </div>
    );
  }

  const data = overview.data;

  return (
    <div className="page">
      <PageHeader
        eyebrow={currentOrg?.name}
        title={`你好，${user?.name ?? '同事'}`}
        description="从知识库开始搭建，所有问答与检索过程都会留下可回放的链路。"
        actions={
          <>
            {can('knowledge:write') && (
              <Link className="btn btn-primary btn-md" to="/knowledge">
                <Plus size={15} />
                <span>新建知识库</span>
              </Link>
            )}
            {can('apps:write') && (
              <Link className="btn btn-secondary btn-md" to="/apps">
                <Bot size={15} />
                <span>创建知识问答</span>
              </Link>
            )}
          </>
        }
      />

      {overview.loading && !data ? (
        <SkeletonRows rows={6} />
      ) : data ? (
        <>
          <div className="stat-grid">
            <StatCard
              icon={<BookOpen size={18} />}
              label="知识库"
              value={data.knowledgeBases}
              to={can('knowledge:read') ? '/knowledge' : undefined}
              hint="可访问的知识库"
            />
            <StatCard
              icon={<FileText size={18} />}
              label="文档"
              value={data.documents}
              hint="已入库与处理中的文档"
            />
            <StatCard icon={<Layers size={18} />} label="知识切片" value={data.chunks} hint="可检索的分块总数" />
            <StatCard
              icon={<Bot size={18} />}
              label="知识问答应用"
              value={data.apps}
              to={can('apps:read') ? '/apps' : undefined}
              hint="当前组织可见的应用"
            />
            <StatCard icon={<Activity size={18} />} label="问答次数" value={data.queries} hint="已记录的问答调用" />
            <StatCard
              icon={<MessageSquareWarning size={18} />}
              label="待处理反馈"
              value={data.feedbackPending}
              to={can('observe:read') ? '/feedback' : undefined}
              hint="点踩与纠错记录"
              tone={data.feedbackPending > 0 ? 'amber' : undefined}
            />
          </div>

          {data.knowledgeBases === 0 && can('knowledge:write') && (
            <Callout
              tone="info"
              title="还没有知识库"
              action={
                <Link to="/knowledge" className="btn btn-primary btn-sm">
                  去创建
                </Link>
              }
            >
              知识库是问答的依据。先创建知识库并上传文档，或者从知识库页面一键创建示例知识库（会真实写入 API）。
            </Callout>
          )}

          <div className="overview-grid">
            <Card className="overview-recent">
              <div className="section-header">
                <div>
                  <h2>最近调用</h2>
                  <p>入库、检索、问答与评测的最新链路。</p>
                </div>
                {can('observe:read') && (
                  <Link to="/traces" className="text-link">
                    全部追踪 <ArrowRight size={14} />
                  </Link>
                )}
              </div>
              {data.recentTraces?.length ? (
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>类型</th>
                        <th>名称</th>
                        <th>状态</th>
                        <th>耗时</th>
                        <th>时间</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.recentTraces.map((trace) => (
                        <tr key={trace.id}>
                          <td>
                            <Badge tone="neutral">{traceKindLabel(trace.kind)}</Badge>
                          </td>
                          <td>
                            {can('observe:read') ? (
                              <Link to={`/traces/${trace.id}`} className="table-link">
                                {trace.name || '未命名调用'}
                              </Link>
                            ) : (
                              trace.name || '未命名调用'
                            )}
                          </td>
                          <td>
                            <StatusBadge status={trace.status} />
                          </td>
                          <td className="mono-cell">{formatDuration(trace.durationMs)}</td>
                          <td className="muted-cell">{formatDateTime(trace.createdAt)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState
                  compact
                  icon={<Activity size={20} />}
                  title="暂无调用记录"
                  description="完成一次检索测试或知识问答后，这里会出现可回放的链路。"
                />
              )}
            </Card>

            <Card className="overview-guide">
              <div className="section-header">
                <div>
                  <h2>搭建路径</h2>
                  <p>按顺序完成配置，避免在缺少依据时直接发布。</p>
                </div>
              </div>
              <ol className="guide-list">
                <GuideItem
                  index="01"
                  icon={<BookOpen size={16} />}
                  title="准备知识库"
                  description="上传文档，检查分块与引用定位。"
                  done={data.knowledgeBases > 0}
                  to={can('knowledge:read') ? '/knowledge' : undefined}
                />
                <GuideItem
                  index="02"
                  icon={<Database size={16} />}
                  title="连接可用模型"
                  description="接入 OpenAI 兼容的 Chat 与 Embedding 模型。"
                  to={can('models:manage') ? '/models' : undefined}
                  done={false}
                  hint={can('models:manage') ? '确认模型可用并完成测试' : '联系系统或组织管理员'}
                />
                <GuideItem
                  index="03"
                  icon={<Bot size={16} />}
                  title="配置并调试问答"
                  description="选择知识库、模型与检索参数，草稿调试后发布。"
                  done={data.apps > 0}
                  to={can('apps:read') ? '/apps' : undefined}
                />
                <GuideItem
                  index="04"
                  icon={<FlaskConical size={16} />}
                  title="建立评测基线"
                  description="导入问题、运行两套配置，再据实说明效果。"
                  to={can('eval:manage') ? '/evals' : undefined}
                  done={false}
                  hint={can('eval:manage') ? '评测指标需结合业务确认' : '需要评测权限'}
                />
              </ol>
              <div className="guide-foot">
                <CircleHelp size={14} />
                所有数字均来自当前组织的真实接口，不包含演示计数。
              </div>
            </Card>
          </div>
        </>
      ) : null}
    </div>
  );
}

function StatCard({
  icon,
  label,
  value,
  hint,
  to,
  tone,
}: {
  icon: ReactNode;
  label: string;
  value: number;
  hint: string;
  to?: string;
  tone?: 'amber';
}) {
  const content = (
    <>
      <div className={`stat-icon${tone === 'amber' ? ' stat-icon-amber' : ''}`}>{icon}</div>
      <div className="stat-copy">
        <span>{label}</span>
        <strong>{formatNumber(value)}</strong>
        <small>{hint}</small>
      </div>
      {to && <ArrowRight size={15} className="stat-arrow" />}
    </>
  );
  if (to) {
    return (
      <Link to={to} className="stat-card">
        {content}
      </Link>
    );
  }
  return <div className="stat-card">{content}</div>;
}

function GuideItem({
  index,
  icon,
  title,
  description,
  done,
  to,
  hint,
}: {
  index: string;
  icon: ReactNode;
  title: string;
  description: string;
  done?: boolean;
  to?: string;
  hint?: string;
}) {
  const body = (
    <div className="guide-item">
      <span className="guide-index">{index}</span>
      <span className="guide-icon">{icon}</span>
      <span className="guide-copy">
        <strong>{title}</strong>
        <small>{description}</small>
      </span>
      {done ? <Badge tone="green">已完成</Badge> : hint ? <span className="guide-hint">{hint}</span> : null}
    </div>
  );
  if (to) return <li><Link to={to}>{body}</Link></li>;
  return <li>{body}</li>;
}

function traceKindLabel(kind: string): string {
  return ({ ingest: '入库', search: '检索', chat: '问答', eval: '评测' } as Record<string, string>)[kind] ?? kind;
}
