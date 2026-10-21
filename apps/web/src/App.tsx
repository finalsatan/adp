import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { AlertCircle, Loader2, RefreshCw } from 'lucide-react';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { ToastProvider } from './contexts/ToastContext';
import { BrandMark, Button, ConfirmProvider } from './components/ui';
import { AppShell } from './layout/AppShell';
import { LoginPage, SetupPage } from './pages/auth/AuthPages';
import { OverviewPage } from './pages/OverviewPage';
import { KnowledgeListPage } from './pages/knowledge/KnowledgeListPage';
import { KnowledgeDetailPage } from './pages/knowledge/KnowledgeDetailPage';
import { DocumentPage } from './pages/knowledge/DocumentPage';
import { ModelsPage } from './pages/models/ModelsPage';
import { AppsPage } from './pages/apps/AppsPage';
import { AppEditorPage } from './pages/apps/AppEditorPage';
import { ChatPage } from './pages/chat/ChatPage';
import { ApiKeysPage } from './pages/keys/ApiKeysPage';
import { TracesPage, TraceDetailPage } from './pages/traces/TracesPage';
import { FeedbackPage } from './pages/feedback/FeedbackPage';
import { EvalListPage } from './pages/evals/EvalListPage';
import { EvalSetPage } from './pages/evals/EvalSetPage';
import { EvalRunPage } from './pages/evals/EvalRunPage';
import { SystemPage } from './pages/system/SystemPage';

function BootSplash() {
  return (
    <div className="boot-screen">
      <BrandMark size="lg" />
      <div className="boot-copy">
        <strong>ADP 智能体开发平台</strong>
        <span>正在连接服务并读取会话…</span>
      </div>
      <Loader2 size={20} className="spin" />
    </div>
  );
}

function BootError({ message }: { message: string }) {
  const auth = useAuth();
  return (
    <div className="boot-screen boot-error">
      <BrandMark size="lg" />
      <div className="boot-copy">
        <strong>无法初始化前端</strong>
        <span>{message}</span>
        <span className="boot-hint">请确认 API 服务运行在 3001 端口，并通过 Vite 的 /api 代理访问。</span>
      </div>
      <Button variant="secondary" icon={<RefreshCw size={15} />} onClick={() => void auth.refresh()}>
        重试
      </Button>
    </div>
  );
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const auth = useAuth();
  const location = useLocation();
  if (auth.loading) return <BootSplash />;
  if (!auth.initialized) return <Navigate to="/setup" replace />;
  if (!auth.user) {
    const redirect = encodeURIComponent(`${location.pathname}${location.search}`);
    return <Navigate to={`/login?redirect=${redirect}`} replace />;
  }
  return <>{children}</>;
}

function HomeRedirect() {
  const { can } = useAuth();
  return <Navigate to={can('knowledge:read') ? '/knowledge' : '/overview'} replace />;
}

function NotFoundPage() {
  return (
    <div className="page">
      <div className="not-found">
        <AlertCircle size={28} />
        <h1>页面不存在</h1>
        <p>地址可能已变更，或你没有访问该资源的权限。</p>
        <Button variant="secondary" onClick={() => window.history.back()}>
          返回上一页
        </Button>
      </div>
    </div>
  );
}

function AppRoutes() {
  const auth = useAuth();

  if (auth.loading) return <BootSplash />;
  if (auth.bootError) return <BootError message={auth.bootError} />;

  return (
    <Routes>
      <Route path="/setup" element={<SetupPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/chat/:id" element={<ChatPage />} />

      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route path="/" element={<HomeRedirect />} />
        <Route path="/overview" element={<OverviewPage />} />
        <Route path="/knowledge" element={<KnowledgeListPage />} />
        <Route path="/knowledge/:id" element={<KnowledgeDetailPage />} />
        <Route path="/knowledge/:id/documents/:documentId" element={<DocumentPage />} />
        <Route path="/models" element={<ModelsPage />} />
        <Route path="/apps" element={<AppsPage />} />
        <Route path="/apps/:id" element={<AppEditorPage />} />
        <Route path="/keys" element={<ApiKeysPage />} />
        <Route path="/traces" element={<TracesPage />} />
        <Route path="/traces/:id" element={<TraceDetailPage />} />
        <Route path="/feedback" element={<FeedbackPage />} />
        <Route path="/evals" element={<EvalListPage />} />
        <Route path="/evals/:id" element={<EvalSetPage />} />
        <Route path="/evals/:id/runs/:runId" element={<EvalRunPage />} />
        <Route path="/system" element={<SystemPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

export function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <ConfirmProvider>
          <AppRoutes />
        </ConfirmProvider>
      </ToastProvider>
    </AuthProvider>
  );
}
