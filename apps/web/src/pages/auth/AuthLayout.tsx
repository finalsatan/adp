import type { ReactNode } from 'react';
import { BookOpen, FlaskConical, Quote, ShieldCheck } from 'lucide-react';
import { BrandMark } from '../../components/ui';
import { useAuth } from '../../contexts/AuthContext';

export function AuthLayout({
  title,
  description,
  children,
  footer,
}: {
  title: string;
  description: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const { branding } = useAuth();

  return (
    <div className="auth-page">
      <section className="auth-aside">
        <div className="auth-aside-top">
          <BrandMark size="lg" />
          <div>
            <strong>{branding.name}</strong>
            <span>{branding.subtitle}</span>
          </div>
        </div>
        <div className="auth-aside-copy">
          <span className="auth-kicker">KNOWLEDGE · ANSWERS · OBSERVABILITY</span>
          <h1>
            让每一次回答
            <br />
            都有依据，可被验证。
          </h1>
          <p>
            面向公司内部的智能体开发平台。从文档入库、带引用问答，到用户反馈与效果评测，
            保留完整链路。
          </p>
        </div>
        <div className="auth-feature-list">
          <div className="auth-feature">
            <BookOpen size={18} />
            <div>
              <strong>知识库与检索</strong>
              <span>结构、语义、父子分块，混合检索与阶段得分</span>
            </div>
          </div>
          <div className="auth-feature">
            <Quote size={18} />
            <div>
              <strong>回答引用可定位</strong>
              <span>终端用户点击引用即可查看被引用的原始片段</span>
            </div>
          </div>
          <div className="auth-feature">
            <FlaskConical size={18} />
            <div>
              <strong>反馈进入评测闭环</strong>
              <span>链路追踪、点踩纠错、两套检索配置对比</span>
            </div>
          </div>
        </div>
        <div className="auth-aside-foot">
          <ShieldCheck size={14} />
          公司内网私有化部署 · 组织数据隔离 · 密钥加密存储
        </div>
      </section>

      <section className="auth-main">
        <div className="auth-card">
          <div className="auth-card-brand">
            <BrandMark size="sm" />
            <span>{branding.name} · {branding.subtitle}</span>
          </div>
          <div className="auth-heading">
            <h2>{title}</h2>
            <p>{description}</p>
          </div>
          {children}
          {footer && <div className="auth-footer">{footer}</div>}
        </div>
      </section>
    </div>
  );
}
