import type { ModelRow } from '../db/index.js';
import type { SecretBox } from '../lib/util.js';
import { maskSecret } from '../lib/util.js';

export function openModelKey(secretBox: SecretBox, model: ModelRow): string | undefined {
  if (!model.api_key_enc) return undefined;
  return secretBox.decrypt(model.api_key_enc);
}

export function serializeModel(model: ModelRow, secretBox: SecretBox) {
  let masked = '';
  if (model.api_key_enc) {
    try {
      masked = maskSecret(secretBox.decrypt(model.api_key_enc));
    } catch {
      masked = '****';
    }
  }
  return {
    id: model.id,
    name: model.name,
    kind: model.kind,
    provider: model.provider,
    baseUrl: model.base_url,
    model: model.model,
    scope: model.scope,
    orgId: model.org_id,
    enabled: model.enabled === 1,
    apiKeyMasked: masked,
    dimensions: model.dimensions ?? undefined,
    createdAt: model.created_at,
  };
}
