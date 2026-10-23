import type { components } from "../../api/schema";

export type Model = components["schemas"]["ModelOut"];
export type ChatParams = components["schemas"]["PlaygroundParams"];
export type ChatRequestMessage = components["schemas"]["PlaygroundMessage"];
export type ChatRole = ChatRequestMessage["role"];

export interface ChatStats {
  firstTokenMs: number;
  totalMs: number;
  inputTokens: number;
  outputTokens: number;
}

/** 一列里的一条消息（用户或模型回答）。 */
export interface ChatMessage {
  role: ChatRole;
  content: string;
  reasoning: string;
  stats: ChatStats | null;
  error: string | null;
}

/** 参数浮层里本次体验的参数。 */
export interface PlaygroundParamsState {
  temperature: number;
  topP: number;
  maxTokens: number | null;
  thinking: boolean;
}

export interface PlaygroundColumnState {
  key: number;
  modelId: number | null;
  params: PlaygroundParamsState;
  messages: ChatMessage[];
  streaming: boolean;
}

const FALLBACK_TEMPERATURE = 0.7;
const FALLBACK_TOP_P = 0.9;

function numberOrDefault(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

/** 默认值取自模型配置（温度、Top P、最大输出），深度思考默认关闭。 */
export function defaultParams(model: Model): PlaygroundParamsState {
  const defaults = model.default_params ?? {};
  return {
    temperature: numberOrDefault(defaults.temperature, FALLBACK_TEMPERATURE),
    topP: numberOrDefault(defaults.top_p, FALLBACK_TOP_P),
    maxTokens: model.max_output,
    thinking: false,
  };
}

export function createColumn(key: number): PlaygroundColumnState {
  return {
    key,
    modelId: null,
    params: {
      temperature: FALLBACK_TEMPERATURE,
      topP: FALLBACK_TOP_P,
      maxTokens: null,
      thinking: false,
    },
    messages: [],
    streaming: false,
  };
}

export function userMessage(content: string): ChatMessage {
  return { role: "user", content, reasoning: "", stats: null, error: null };
}

export function assistantMessage(): ChatMessage {
  return { role: "assistant", content: "", reasoning: "", stats: null, error: null };
}

/** 本次体验参数转成接口结构；未设置的字段省略。 */
export function toRequestParams(params: PlaygroundParamsState): ChatParams {
  return {
    temperature: params.temperature,
    top_p: params.topP,
    max_tokens: params.maxTokens ?? undefined,
    thinking: params.thinking,
  };
}

/** 每列只带自己这一列的历史；跳过还没生成出内容的模型回答。 */
export function toRequestMessages(messages: ChatMessage[]): ChatRequestMessage[] {
  return messages
    .filter((message) => message.content.length > 0)
    .map((message) => ({ role: message.role, content: message.content }));
}

/** 毫秒转秒：620 → “0.62s”，3100 → “3.1s”，3000 → “3s”。 */
export function formatSeconds(ms: number): string {
  return `${(ms / 1000).toFixed(2).replace(/\.?0+$/, "")}s`;
}
