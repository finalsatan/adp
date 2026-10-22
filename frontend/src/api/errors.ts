/** 后端错误响应结构与统一的错误对象。 */

export interface ApiErrorBody {
  code?: string;
  message?: string;
  detail?: unknown;
  request_id?: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly detail?: unknown;
  readonly requestId?: string;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message && body.message.length > 0 ? body.message : "请求失败，请稍后再试");
    this.name = "ApiError";
    this.status = status;
    this.code = body.code;
    this.detail = body.detail;
    this.requestId = body.request_id;
  }

  static from(status: number, body: unknown): ApiError {
    if (body && typeof body === "object") {
      return new ApiError(status, body as ApiErrorBody);
    }
    return new ApiError(status, {});
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/** 取后端返回的中文说明；没有时用兜底文案。 */
export function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  return "请求失败，请稍后再试";
}

/** 读取（克隆的）错误响应体，原响应仍可交给调用方处理。 */
export async function readErrorBody(response: Response): Promise<ApiErrorBody> {
  try {
    const body: unknown = await response.clone().json();
    if (body && typeof body === "object") {
      return body as ApiErrorBody;
    }
  } catch {
    // 非 JSON 响应体，忽略
  }
  return {};
}

declare module "@tanstack/react-query" {
  interface Register {
    queryMeta: {
      /** 表单页自行显示错误时置 true，全局 onError 不再弹提示。 */
      skipErrorToast?: boolean;
    };
    mutationMeta: {
      skipErrorToast?: boolean;
    };
  }
}
