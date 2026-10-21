import type { ParsedElement } from './parser.js';
import { cosine, safeSentenceUnits, splitLongText, tailOverlap } from '../lib/text.js';

export type ChunkStrategy = 'structure' | 'semantic' | 'parent-child';

export interface ChunkConfig {
  strategy: ChunkStrategy;
  maxChars: number;
  overlap: number;
}

export interface ChunkSourceRef {
  page?: number;
  sheet?: string;
  row?: number;
  paragraph?: number;
  heading?: string;
}

export interface DraftChunk {
  content: string;
  headingPath: string[];
  sourceRef: ChunkSourceRef;
  isParent: boolean;
  parentIndex?: number;
  /** Internal parts used to build parent-child children without losing table metadata. */
  parts?: Unit[];
}

interface Unit {
  text: string;
  headingPath: string[];
  sourceRef: ChunkSourceRef;
  isTableRow?: boolean;
  tableHeader?: string;
}

function sourceRefOf(element: ParsedElement): ChunkSourceRef {
  const ref: ChunkSourceRef = {};
  if (typeof element.page === 'number') ref.page = element.page;
  if (typeof element.sheet === 'string') ref.sheet = element.sheet;
  if (typeof element.row === 'number') ref.row = element.row;
  if (typeof element.paragraph === 'number') ref.paragraph = element.paragraph;
  if (element.headingPath && element.headingPath.length > 0) {
    ref.heading = element.headingPath[element.headingPath.length - 1];
  }
  return ref;
}

function tableUnitsFromText(
  text: string,
  headingPath: string[],
  sourceRef: ChunkSourceRef,
  explicitHeader?: string,
): Unit[] {
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length === 0) return [];
  if (explicitHeader) {
    return lines.map((line) => ({
      text: line,
      headingPath,
      sourceRef,
      isTableRow: true,
      tableHeader: explicitHeader,
    }));
  }
  const first = lines[0]!;
  const second = lines[1];
  const isSeparator = second ? /^\|?\s*:?-{3,}/.test(second) : false;
  const header = isSeparator ? `${first}\n${second}` : first;
  const dataRows = lines.slice(isSeparator ? 2 : 1);
  if (dataRows.length === 0) {
    return [{ text: header, headingPath, sourceRef }];
  }
  return dataRows.map((row) => ({
    text: row,
    headingPath,
    sourceRef,
    isTableRow: true,
    tableHeader: header,
  }));
}

export function elementsToUnits(elements: ParsedElement[]): Unit[] {
  const units: Unit[] = [];
  let headingStack: string[] = [];
  let currentTableHeader: string | undefined;
  const sorted = [...elements].sort((a, b) => a.order - b.order);

  for (const element of sorted) {
    const text = element.text?.trim();
    if (!text) continue;
    if (element.type === 'heading') {
      headingStack = element.headingPath && element.headingPath.length > 0
        ? [...element.headingPath]
        : [...headingStack, text];
      currentTableHeader = undefined;
    } else if (element.headingPath && element.headingPath.length > 0) {
      headingStack = [...element.headingPath];
    }

    if (element.type === 'table_header') {
      currentTableHeader = text;
      continue;
    }
    if (element.type === 'table_row') {
      units.push({
        text,
        headingPath: [...headingStack],
        sourceRef: sourceRefOf(element),
        isTableRow: true,
        tableHeader: element.tableHeader?.trim() || currentTableHeader,
      });
      continue;
    }
    if (element.type === 'table') {
      units.push(...tableUnitsFromText(text, [...headingStack], sourceRefOf(element), element.tableHeader));
      currentTableHeader = undefined;
      continue;
    }

    currentTableHeader = undefined;
    units.push({
      text,
      headingPath: [...headingStack],
      sourceRef: sourceRefOf(element),
    });
  }
  return units;
}

function pathKey(path: string[]): string {
  return JSON.stringify(path);
}

function packUnits(units: Unit[], maxChars: number, overlap: number): DraftChunk[] {
  const chunks: DraftChunk[] = [];
  let currentText = '';
  let currentPath: string[] = [];
  let currentPathKey = '';
  let currentSource: ChunkSourceRef = {};
  let currentParts: Unit[] = [];
  let pendingOverlap = '';
  let currentTableHeader: string | null = null;

  const flush = (preserveOverlap: boolean) => {
    if (!currentText.trim()) {
      currentText = '';
      currentParts = [];
      return;
    }
    const content = currentText.trim();
    chunks.push({
      content,
      headingPath: currentPath,
      sourceRef: currentSource,
      isParent: false,
      parts: currentParts,
    });
    pendingOverlap = preserveOverlap ? tailOverlap(content, overlap) : '';
    currentText = '';
    currentSource = {};
    currentParts = [];
  };

  for (const unit of units) {
    const key = pathKey(unit.headingPath);

    if (unit.isTableRow) {
      const header = unit.tableHeader?.trim() ?? '';
      const row = unit.text.trim();
      if (!row) continue;
      if (currentText && (currentTableHeader !== header || currentPathKey !== key)) {
        flush(false);
        pendingOverlap = '';
      }
      currentPath = unit.headingPath;
      currentPathKey = key;
      currentTableHeader = header;
      const block = header ? `${header}\n${row}` : row;
      const part: Unit = { ...unit, text: row };
      if (!currentText) {
        currentText = block;
        currentSource = unit.sourceRef;
        currentParts = [part];
      } else if (currentText.length + row.length + 1 <= maxChars) {
        currentText = `${currentText}\n${row}`;
        currentParts.push(part);
      } else {
        // A long table row is never cut. Each new table chunk repeats its header.
        flush(false);
        pendingOverlap = '';
        currentText = block;
        currentSource = unit.sourceRef;
        currentParts = [part];
      }
      continue;
    }

    if (currentTableHeader !== null) {
      flush(false);
      pendingOverlap = '';
      currentTableHeader = null;
    }

    if (currentText && key !== currentPathKey) {
      flush(false);
      pendingOverlap = '';
    }
    currentPath = unit.headingPath;
    currentPathKey = key;
    const segments = splitLongText(unit.text, maxChars);
    for (const segment of segments) {
      let seed = '';
      if (!currentText && pendingOverlap && pendingOverlap !== segment.slice(0, pendingOverlap.length)) {
        const room = maxChars - segment.length - 1;
        if (room > 0) seed = pendingOverlap.slice(-room);
      }
      const joined = seed ? `${seed}\n${segment}` : segment;
      const part: Unit = { ...unit, text: segment };
      if (currentText && currentText.length + joined.length + 1 > maxChars) {
        flush(true);
        let nextSeed = '';
        const room = maxChars - segment.length - 1;
        if (pendingOverlap && room > 0) nextSeed = pendingOverlap.slice(-room);
        currentText = nextSeed ? `${nextSeed}\n${segment}` : segment;
        currentSource = unit.sourceRef;
        currentParts = [part];
      } else if (!currentText) {
        currentText = joined;
        currentSource = unit.sourceRef;
        currentParts = [part];
      } else {
        currentText = `${currentText}\n${segment}`;
        currentParts.push(part);
      }
    }
  }
  flush(false);
  return chunks;
}

function buildParents(units: Unit[], config: ChunkConfig): DraftChunk[] {
  return packUnits(units, config.maxChars, config.overlap).map((chunk) => ({
    ...chunk,
    isParent: true,
  }));
}

export function chunkStructure(elements: ParsedElement[], config: ChunkConfig): DraftChunk[] {
  return packUnits(elementsToUnits(elements), config.maxChars, config.overlap);
}

export function chunkParentChild(elements: ParsedElement[], config: ChunkConfig): DraftChunk[] {
  const units = elementsToUnits(elements);
  const parents = buildParents(units, config);
  const childMax = Math.max(120, Math.floor(config.maxChars / 2));
  const drafts: DraftChunk[] = [];
  parents.forEach((parent) => {
    const parentDraftIndex = drafts.length;
    drafts.push(parent);
    const sourceParts: Unit[] =
      parent.parts && parent.parts.length > 0
        ? parent.parts
        : [{ text: parent.content, headingPath: parent.headingPath, sourceRef: parent.sourceRef }];
    const childUnits: Unit[] = [];
    for (const part of sourceParts) {
      if (part.isTableRow) {
        childUnits.push(part);
        continue;
      }
      for (const text of splitLongText(part.text, childMax)) {
        childUnits.push({ ...part, text });
      }
    }
    const children = packUnits(childUnits, childMax, Math.min(config.overlap, Math.floor(childMax / 4)));
    for (const child of children) {
      drafts.push({ ...child, isParent: false, parentIndex: parentDraftIndex });
    }
  });
  return drafts;
}

export async function chunkSemantic(
  elements: ParsedElement[],
  config: ChunkConfig,
  embed: (texts: string[]) => Promise<number[][]>,
): Promise<DraftChunk[]> {
  const rawUnits = elementsToUnits(elements);
  const units: Unit[] = [];
  for (const unit of rawUnits) {
    if (unit.isTableRow) {
      units.push(unit);
      continue;
    }
    for (const text of safeSentenceUnits(unit.text, Math.min(600, config.maxChars))) {
      units.push({ ...unit, text });
    }
  }
  if (units.length === 0) return [];
  if (units.length > 4000) {
    throw new Error('语义分块单元超过 4000 个，请增大分块长度或改用结构分块');
  }
  const vectors = await embed(units.map((unit) => unit.text));
  if (vectors.length !== units.length) throw new Error('语义分块 Embedding 数量与文本单元不一致');

  const chunks: DraftChunk[] = [];
  const threshold = 0.58;
  let currentText = '';
  let currentPath: string[] = [];
  let currentSource: ChunkSourceRef = {};
  let previousVector: number[] | null = null;
  let previousPathKey = '';
  let previousWasTable = false;

  const flush = (preserveOverlap: boolean) => {
    if (!currentText.trim()) return;
    chunks.push({
      content: currentText.trim(),
      headingPath: currentPath,
      sourceRef: currentSource,
      isParent: false,
    });
    currentText = preserveOverlap ? tailOverlap(currentText, config.overlap).trim() : '';
  };

  for (let index = 0; index < units.length; index += 1) {
    const unit = units[index]!;
    const vector = vectors[index]!;
    const key = pathKey(unit.headingPath);

    if (unit.isTableRow) {
      flush(false);
      const display = unit.tableHeader ? `${unit.tableHeader}\n${unit.text}` : unit.text;
      chunks.push({
        content: display.trim(),
        headingPath: unit.headingPath,
        sourceRef: unit.sourceRef,
        isParent: false,
      });
      previousVector = vector;
      previousPathKey = key;
      previousWasTable = true;
      continue;
    }
    if (previousWasTable) {
      currentText = '';
      previousWasTable = false;
    }

    const similarity = previousVector ? cosine(previousVector, vector) : 0;
    const sameSection = key === previousPathKey;
    const wouldExceed = currentText.length + unit.text.length + 1 > config.maxChars;
    if (!currentText) {
      currentPath = unit.headingPath;
      currentSource = unit.sourceRef;
      currentText = unit.text;
    } else if (!sameSection || similarity < threshold || wouldExceed) {
      flush(sameSection);
      const room = config.maxChars - unit.text.length - 1;
      const seed = sameSection && currentText && room > 0 ? currentText.slice(-room) : '';
      currentPath = unit.headingPath;
      currentSource = unit.sourceRef;
      currentText = seed ? `${seed}\n${unit.text}` : unit.text;
    } else {
      currentText = `${currentText}\n${unit.text}`;
    }
    previousVector = vector;
    previousPathKey = key;
  }
  flush(false);
  return chunks;
}

export async function chunkDocument(
  elements: ParsedElement[],
  config: ChunkConfig,
  embed: (texts: string[]) => Promise<number[][]>,
): Promise<DraftChunk[]> {
  if (config.strategy === 'semantic') return chunkSemantic(elements, config, embed);
  if (config.strategy === 'parent-child') return chunkParentChild(elements, config);
  return chunkStructure(elements, config);
}
