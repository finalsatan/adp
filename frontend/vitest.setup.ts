import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/react";
import { ConfigProvider } from "antd";

// 本机 jsdom + Ant Design 表单渲染很慢，负载高时异步查询会超过 Testing Library
// 默认的 1 秒（P4-18 实测：单独跑 AppCreateModal 的“重名”用例约 2.2 秒，
// SettingsPage 每个用例约 5.7 秒；再开 4 个 CPU 压力进程时整个文件从 16 秒涨到 55 秒）。
// 统一放宽异步查询超时，并关掉 AntD 动效减少无谓的动画计时器与重渲染。
configure({ asyncUtilTimeout: 10_000 });
ConfigProvider.config({ theme: { token: { motion: false } } });

// antd 的响应式组件需要 matchMedia，jsdom 没有实现（仅测试环境用）。
if (typeof window.matchMedia !== "function") {
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList;
}
