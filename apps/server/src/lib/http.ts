import type { FastifyRequest } from 'fastify';
import { ZodError, type ZodTypeAny, type z } from 'zod';
import type { Caller, SessionCaller } from '../services/access.js';
import { AppError, unauthorized } from './errors.js';

declare module 'fastify' {
  interface FastifyRequest {
    caller?: Caller;
    authError?: AppError;
  }
}

export function requireCaller(request: FastifyRequest): Caller {
  if (request.authError) throw request.authError;
  if (!request.caller) throw unauthorized();
  return request.caller;
}

export function optionalCaller(request: FastifyRequest): Caller | undefined {
  if (request.authError) throw request.authError;
  return request.caller;
}

export function requireSessionCaller(request: FastifyRequest): SessionCaller {
  const caller = requireCaller(request);
  if (caller.kind !== 'session') throw new AppError(403, 'FORBIDDEN', '该接口不接受应用 API Key');
  return caller;
}

export function parse<T extends ZodTypeAny>(schema: T, value: unknown): z.infer<T> {
  try {
    return schema.parse(value);
  } catch (error) {
    if (error instanceof ZodError) {
      const message = error.issues
        .map((issue) => `${issue.path.length > 0 ? issue.path.join('.') : '输入'}: ${issue.message}`)
        .join('；');
      throw new AppError(400, 'VALIDATION_ERROR', message || '输入参数无效', error.issues);
    }
    throw error;
  }
}

export function parseBody<T extends ZodTypeAny>(request: FastifyRequest, schema: T): z.infer<T> {
  return parse(schema, request.body ?? {});
}

export function parseQuery<T extends ZodTypeAny>(request: FastifyRequest, schema: T): z.infer<T> {
  return parse(schema, request.query ?? {});
}

export function parseParams<T extends ZodTypeAny>(request: FastifyRequest, schema: T): z.infer<T> {
  return parse(schema, request.params ?? {});
}

export function pageOptions(request: FastifyRequest, defaultLimit = 50): { limit: number; offset: number } {
  const query = request.query as Record<string, unknown> | undefined;
  const rawLimit = Number(query?.limit ?? defaultLimit);
  const rawOffset = Number(query?.offset ?? 0);
  const limit = Number.isFinite(rawLimit) ? Math.max(1, Math.min(200, Math.trunc(rawLimit))) : defaultLimit;
  const offset = Number.isFinite(rawOffset) ? Math.max(0, Math.trunc(rawOffset)) : 0;
  return { limit, offset };
}
