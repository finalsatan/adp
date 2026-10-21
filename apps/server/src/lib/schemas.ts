import { z } from 'zod';
import { ALL_PERMISSIONS } from './permissions.js';
import { DEFAULT_RETRIEVAL_CONFIG } from '../services/retrieval.js';

export const visibilitySchema = z.enum(['private', 'organization', 'platform']);

export const idParams = z.object({ id: z.string().min(1) });

export const chunkConfigSchema = z
  .object({
    strategy: z.enum(['structure', 'semantic', 'parent-child']).default('structure'),
    maxChars: z.number().int().min(200).max(8000).default(800),
    overlap: z.number().int().min(0).max(2000).default(100),
  })
  .refine((value) => value.overlap < value.maxChars, {
    message: 'overlap 必须小于 maxChars',
    path: ['overlap'],
  });

export const chunkConfigPatchSchema = z.object({
  strategy: z.enum(['structure', 'semantic', 'parent-child']).optional(),
  maxChars: z.number().int().min(200).max(8000).optional(),
  overlap: z.number().int().min(0).max(2000).optional(),
});

export const retrievalConfigSchema = z.object({
  mode: z.enum(['hybrid', 'keyword', 'vector']).default(DEFAULT_RETRIEVAL_CONFIG.mode),
  topK: z.number().int().min(1).max(50).default(DEFAULT_RETRIEVAL_CONFIG.topK),
  threshold: z.number().min(0).max(1).default(0),
  rerankModelId: z.string().min(1).nullable().optional(),
  rewriteModelId: z.string().min(1).nullable().optional(),
});

export const retrievalConfigPatchSchema = retrievalConfigSchema.partial();

export const modelKindSchema = z.enum(['chat', 'embedding', 'rerank', 'vision']);
export const modelProviderSchema = z.enum(['openai', 'demo']);

export const modelCreateSchema = z.object({
  name: z.string().trim().min(1).max(120),
  kind: modelKindSchema,
  provider: modelProviderSchema.default('openai'),
  baseUrl: z.string().trim().max(500).default(''),
  model: z.string().trim().min(1).max(200),
  scope: z.enum(['organization', 'platform']).default('organization'),
  orgId: z.string().min(1).optional(),
  enabled: z.boolean().default(true),
  apiKey: z.string().max(2000).optional(),
  dimensions: z.number().int().min(1).max(20000).nullable().optional(),
});

export const modelPatchSchema = z.object({
  name: z.string().trim().min(1).max(120).optional(),
  baseUrl: z.string().trim().max(500).optional(),
  model: z.string().trim().min(1).max(200).optional(),
  enabled: z.boolean().optional(),
  apiKey: z.string().max(2000).optional(),
  dimensions: z.number().int().min(1).max(20000).nullable().optional(),
});

export const setupSchema = z.object({
  name: z.string().trim().min(1).max(80),
  email: z.string().trim().email().max(200),
  password: z.string().min(6).max(200),
  orgName: z.string().trim().min(1).max(120),
});

export const loginSchema = z.object({
  email: z.string().trim().email().max(200),
  password: z.string().min(1).max(200),
});

export const orgCreateSchema = z.object({
  name: z.string().trim().min(1).max(120),
  parentId: z.string().min(1).nullable().optional(),
});

export const userCreateSchema = z.object({
  name: z.string().trim().min(1).max(80),
  email: z.string().trim().email().max(200),
  password: z.string().min(6).max(200),
  orgId: z.string().min(1),
  roleId: z.string().min(1).optional(),
  isSystemAdmin: z.boolean().optional(),
});

export const roleCreateSchema = z.object({
  name: z.string().trim().min(1).max(120),
  permissions: z.array(z.enum(ALL_PERMISSIONS)).min(0),
  global: z.boolean().default(false),
  orgId: z.string().min(1).optional(),
});

export const memberAddSchema = z.object({
  userId: z.string().min(1),
  roleId: z.string().min(1),
});

export const kbCreateSchema = z.object({
  name: z.string().trim().min(1).max(200),
  description: z.string().max(5000).default(''),
  visibility: visibilitySchema.default('private'),
  embeddingModelId: z.string().min(1).optional(),
  chunkConfig: chunkConfigSchema.optional(),
  retrievalConfig: retrievalConfigSchema.optional(),
});

export const kbPatchSchema = z.object({
  name: z.string().trim().min(1).max(200).optional(),
  description: z.string().max(5000).optional(),
  visibility: visibilitySchema.optional(),
  embeddingModelId: z.string().min(1).nullable().optional(),
  chunkConfig: chunkConfigPatchSchema.optional(),
  retrievalConfig: retrievalConfigPatchSchema.optional(),
});

export const grantSchema = z.object({
  userId: z.string().min(1),
  level: z.enum(['view', 'edit', 'grant']),
});

export const documentTextSchema = z.object({
  name: z.string().trim().min(1).max(200),
  content: z.string().max(20 * 1024 * 1024),
  mime: z.string().max(200).optional(),
});

export const chunkPatchSchema = z.object({
  content: z.string().trim().min(1).max(50_000).optional(),
  enabled: z.boolean().optional(),
});

export const chunkSplitSchema = z.object({
  offset: z.number().int().min(1).max(100_000),
});

export const chunkMergeSchema = z.object({
  nextChunkId: z.string().min(1),
});

export const searchSchema = z.object({
  query: z.string().trim().min(1).max(5000),
  config: retrievalConfigSchema.partial().optional(),
});

export const appCreateSchema = z.object({
  name: z.string().trim().min(1).max(200),
  description: z.string().max(5000).default(''),
  visibility: visibilitySchema.default('private'),
  kbIds: z.array(z.string().min(1)).max(50).default([]),
  chatModelId: z.string().min(1),
  systemPrompt: z.string().max(20_000).default(''),
  retrievalConfig: retrievalConfigSchema.optional(),
});

export const appPatchSchema = z.object({
  name: z.string().trim().min(1).max(200).optional(),
  description: z.string().max(5000).optional(),
  visibility: visibilitySchema.optional(),
  kbIds: z.array(z.string().min(1)).max(50).optional(),
  chatModelId: z.string().min(1).optional(),
  systemPrompt: z.string().max(20_000).optional(),
  retrievalConfig: retrievalConfigPatchSchema.optional(),
});

export const conversationCreateSchema = z.object({
  title: z.string().trim().max(200).optional(),
});

export const chatSchema = z.object({
  message: z.string().trim().min(1).max(20_000),
  conversationId: z.string().min(1).optional(),
  draft: z.boolean().default(false),
  stream: z.boolean().default(false),
});

export const feedbackSchema = z.object({
  rating: z.union([z.literal(1), z.literal(-1)]),
  correction: z.string().max(10_000).optional(),
});

export const apiKeyCreateSchema = z.object({
  name: z.string().trim().min(1).max(120),
  appId: z.string().min(1),
});

export const evalSetCreateSchema = z.object({
  name: z.string().trim().min(1).max(200),
  description: z.string().max(5000).default(''),
  appId: z.string().min(1),
});

export const evalCaseInputSchema = z.object({
  question: z.string().trim().min(1).max(5000),
  expectedAnswer: z.string().max(20_000).default(''),
  documentId: z.string().min(1).nullable().optional(),
  tags: z.array(z.string().max(80)).max(30).optional(),
});

export const evalCasesCreateSchema = z.union([
  evalCaseInputSchema,
  z.object({ cases: z.array(evalCaseInputSchema).min(1).max(500) }),
]);

export const evalGenerateSchema = z.object({
  count: z.number().int().min(1).max(50).default(5),
  kbId: z.string().min(1),
});

export const evalFromFeedbackSchema = z.object({
  feedbackId: z.string().min(1),
  expectedAnswer: z.string().max(20_000).optional(),
});

export const evalReviewSchema = z.object({
  variant: z.enum(['A', 'B']),
  answerCorrect: z.boolean(),
  citationsCorrect: z.boolean(),
  comment: z.string().max(10_000).optional(),
});

export const evalRunCreateSchema = z.object({
  name: z.string().trim().max(200).optional(),
  configA: retrievalConfigSchema,
  configB: retrievalConfigSchema.optional(),
});
