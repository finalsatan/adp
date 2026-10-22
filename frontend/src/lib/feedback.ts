import type { MessageInstance } from "antd/es/message/interface";

let messageApi: MessageInstance | null = null;

/** 由 App 内的桥接组件注入 message 实例（静态函数读不到主题上下文）。 */
export function setMessageApi(api: MessageInstance): void {
  messageApi = api;
}

export function notifyError(text: string): void {
  void messageApi?.error(text);
}
