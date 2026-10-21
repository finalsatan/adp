export class AppError extends Error {
  readonly statusCode: number;
  readonly code: string;
  readonly details?: unknown;

  constructor(statusCode: number, code: string, message: string, details?: unknown) {
    super(message);
    this.name = 'AppError';
    this.statusCode = statusCode;
    this.code = code;
    this.details = details;
  }
}

export class ModelError extends AppError {
  constructor(message: string, details?: unknown) {
    super(502, 'MODEL_ERROR', message, details);
    this.name = 'ModelError';
  }
}

export class ParserError extends AppError {
  constructor(message: string, details?: unknown) {
    super(502, 'PARSER_ERROR', message, details);
    this.name = 'ParserError';
  }
}

export function notFound(what = '资源'): AppError {
  return new AppError(404, 'NOT_FOUND', `${what}不存在或无权访问`);
}

export function forbidden(message = '没有执行该操作的权限'): AppError {
  return new AppError(403, 'FORBIDDEN', message);
}

export function badRequest(message: string, code = 'BAD_REQUEST'): AppError {
  return new AppError(400, code, message);
}

export function conflict(message: string): AppError {
  return new AppError(409, 'CONFLICT', message);
}

export function unauthorized(message = '请先登录'): AppError {
  return new AppError(401, 'UNAUTHORIZED', message);
}
