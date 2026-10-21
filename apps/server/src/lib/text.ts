export interface ScoredText {
  text: string;
  index: number;
}

/** CJK unigrams + bigrams plus latin/number words. Deliberately dependency-free. */
export function tokenize(text: string): string[] {
  const lower = text.toLowerCase().normalize('NFKC');
  const tokens: string[] = [];
  for (const word of lower.match(/[a-z0-9_]+/g) ?? []) tokens.push(`w:${word}`);
  const chinese = [...lower].filter((ch) => /[\u3400-\u9fff]/.test(ch));
  for (const ch of chinese) tokens.push(`c:${ch}`);
  for (let i = 0; i < chinese.length - 1; i += 1) tokens.push(`b:${chinese[i]}${chinese[i + 1]}`);
  return tokens;
}

export function termFrequency(tokens: string[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const token of tokens) counts.set(token, (counts.get(token) ?? 0) + 1);
  return counts;
}

export function cosine(a: readonly number[], b: readonly number[]): number {
  const length = Math.min(a.length, b.length);
  let dot = 0;
  let normA = 0;
  let normB = 0;
  for (let i = 0; i < length; i += 1) {
    const x = a[i] ?? 0;
    const y = b[i] ?? 0;
    dot += x * y;
    normA += x * x;
    normB += y * y;
  }
  if (normA === 0 || normB === 0) return 0;
  return dot / (Math.sqrt(normA) * Math.sqrt(normB));
}

export function normalizeScores<T extends { score: number }>(items: T[]): T[] {
  const max = items.reduce((value, item) => Math.max(value, item.score), 0);
  if (max <= 0) return items;
  return items.map((item) => ({ ...item, score: item.score / max }));
}

/** Character-level F1 after removing whitespace/punctuation. Used only as a reference metric. */
export function lexicalF1(answer: string, expected: string): number {
  const normalize = (value: string) =>
    value
      .toLowerCase()
      .replace(/[\s\p{P}\p{S}]+/gu, '')
      .trim();
  const a = normalize(answer);
  const b = normalize(expected);
  if (!a && !b) return 1;
  if (!a || !b) return 0;
  const aTokens = a.length <= 1 ? [a] : [...a];
  const bTokens = b.length <= 1 ? [b] : [...b];
  const counts = new Map<string, number>();
  for (const token of bTokens) counts.set(token, (counts.get(token) ?? 0) + 1);
  let overlap = 0;
  for (const token of aTokens) {
    const remaining = counts.get(token) ?? 0;
    if (remaining > 0) {
      overlap += 1;
      counts.set(token, remaining - 1);
    }
  }
  if (overlap === 0) return 0;
  const precision = overlap / aTokens.length;
  const recall = overlap / bTokens.length;
  return (2 * precision * recall) / (precision + recall);
}

export function truncateText(text: string, maxChars: number): string {
  if (text.length <= maxChars) return text;
  return `${text.slice(0, Math.max(0, maxChars - 1))}…`;
}

export function splitLongText(text: string, maxChars: number): string[] {
  const clean = text.replace(/\r\n?/g, '\n').replace(/[ \t]+\n/g, '\n').trim();
  if (!clean) return [];
  if (clean.length <= maxChars) return [clean];

  const pieces: string[] = [];
  const paragraphs = clean.split(/\n{2,}/);
  for (const paragraph of paragraphs) {
    const trimmed = paragraph.trim();
    if (!trimmed) continue;
    if (trimmed.length <= maxChars) {
      pieces.push(trimmed);
      continue;
    }
    const sentences =
      trimmed.match(/[^。！？!?；;\n]+[。！？!?；;\n]*/g)?.map((sentence) => sentence.trim()).filter(Boolean) ?? [];
    const units = sentences.length > 0 ? sentences : [trimmed];
    let current = '';
    for (const unit of units) {
      if (unit.length > maxChars) {
        if (current) {
          pieces.push(current);
          current = '';
        }
        for (let offset = 0; offset < unit.length; offset += maxChars) {
          pieces.push(unit.slice(offset, offset + maxChars));
        }
        continue;
      }
      if (current && current.length + unit.length + 1 > maxChars) {
        pieces.push(current);
        current = unit;
      } else {
        current = current ? `${current}\n${unit}` : unit;
      }
    }
    if (current) pieces.push(current);
  }
  return pieces;
}

export function tailOverlap(text: string, overlap: number): string {
  if (overlap <= 0) return '';
  if (text.length <= overlap) return text;
  return text.slice(-overlap);
}

export function safeSentenceUnits(text: string, targetChars: number): string[] {
  const paragraphs = text
    .replace(/\r\n?/g, '\n')
    .split(/\n{2,}/)
    .map((item) => item.trim())
    .filter(Boolean);
  const units: string[] = [];
  for (const paragraph of paragraphs) {
    if (paragraph.length <= targetChars) {
      units.push(paragraph);
      continue;
    }
    const sentences =
      paragraph.match(/[^。！？!?；;\n]+[。！？!?；;\n]*/g)?.map((item) => item.trim()).filter(Boolean) ?? [];
    let current = '';
    for (const sentence of sentences.length > 0 ? sentences : [paragraph]) {
      if (sentence.length > targetChars) {
        if (current) {
          units.push(current);
          current = '';
        }
        for (let offset = 0; offset < sentence.length; offset += targetChars) {
          units.push(sentence.slice(offset, offset + targetChars));
        }
      } else if (current && current.length + sentence.length > targetChars) {
        units.push(current);
        current = sentence;
      } else {
        current = current ? `${current}${sentence}` : sentence;
      }
    }
    if (current) units.push(current);
  }
  return units;
}
