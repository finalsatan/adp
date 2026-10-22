import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { App as AntApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { useEffect } from "react";
import { BrowserRouter, useNavigate } from "react-router-dom";

import { setNavigate } from "./api/client";
import { getErrorMessage } from "./api/errors";
import { notifyError, setMessageApi } from "./lib/feedback";
import { AppRoutes } from "./router";

const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (error, query) => {
      if (query.meta?.skipErrorToast) {
        return;
      }
      notifyError(getErrorMessage(error));
    },
  }),
  mutationCache: new MutationCache({
    onError: (error, _variables, _context, mutation) => {
      if (mutation.meta?.skipErrorToast) {
        return;
      }
      notifyError(getErrorMessage(error));
    },
  }),
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: false,
    },
  },
});

/** 把 react-router 的跳转和 antd 的 message 实例注入非组件模块。 */
function Bridges() {
  const navigate = useNavigate();
  const { message } = AntApp.useApp();

  useEffect(() => {
    setNavigate((to) => navigate(to));
  }, [navigate]);

  useEffect(() => {
    setMessageApi(message);
  }, [message]);

  return null;
}

export default function App() {
  return (
    <ConfigProvider locale={zhCN}>
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <Bridges />
            <AppRoutes />
          </BrowserRouter>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  );
}
