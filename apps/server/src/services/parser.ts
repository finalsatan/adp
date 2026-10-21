import path from 'node:path';
import { ParserError } from '../lib/errors.js';
import { redactSecrets } from '../lib/util.js';

export interface ParsedElement {
  id: string;
  type: string;
  text: string;
  level?: number;
  page?: number;
  sheet?: string;
  row?: number;
  paragraph?: number;
  headingPath?: string[];
  tableHeader?: string;
  order: number;
}

export interface ParseResult {
  schemaVersion: 1;
  text: string;
  elements: ParsedElement[];
  engine: string;
  warnings: string[];
}

export interface ParseInput {
  fileName: string;
  mime?: string;
  buffer?: Buffer;
  text?: string;
}

export interface ParserOptions {
  parserUrl: string;
  parserToken?: string;
  timeoutMs: number;
}

/** The extension is authoritative: a text/* label must never route PDF/Office to the local text parser. */
function textualKind(fileName: string): 'markdown' | 'text' | 'csv' | 'json' | null {
  const ext = path.extname(fileName).toLowerCase();
  if (ext === '.md' || ext === '.markdown') return 'markdown';
  if (ext === '.txt') return 'text';
  if (ext === '.csv') return 'csv';
  if (ext === '.json') return 'json';
  return null;
}

function element(
  id: string,
  type: string,
  text: string,
  order: number,
  extras: Partial<ParsedElement> = {},
): ParsedElement {
  return { id, type, text, order, ...extras };
}

export function parseLocalText(
  fileName: string,
  content: string,
  kind: 'markdown' | 'text' | 'csv' | 'json',
): ParseResult {
  const normalized = content.replace(/\r\n?/g, '\n').replace(/\u0000/g, '');
  if (!normalized.trim()) throw new ParserError(`${fileName} 中没有可提取的文字`);
  const elements: ParsedElement[] = [];
  let paragraph = 0;
  let order = 0;

  if (kind === 'markdown') {
    const headingStack: Array<{ level: number; text: string }> = [];
    const lines = normalized.split('\n');
    let buffer: string[] = [];
    let paragraphStart = 0;
    const flush = () => {
      const text = buffer.join('\n').trim();
      if (text) {
        paragraph += 1;
        elements.push(
          element(`el-${order + 1}`, 'paragraph', text, order++, {
            paragraph: paragraphStart || paragraph,
            headingPath: headingStack.map((item) => item.text),
          }),
        );
      }
      buffer = [];
      paragraphStart = 0;
    };
    const isTableLine = (line: string) => /^\s*\|.*\|\s*$/.test(line);
    for (let index = 0; index < lines.length; index += 1) {
      const line = lines[index]!;
      const heading = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
      if (heading) {
        flush();
        const level = heading[1]!.length;
        const text = heading[2]!.trim();
        while (headingStack.length > 0 && headingStack[headingStack.length - 1]!.level >= level) {
          headingStack.pop();
        }
        headingStack.push({ level, text });
        paragraph += 1;
        elements.push(
          element(`el-${order + 1}`, 'heading', text, order++, {
            level,
            paragraph,
            headingPath: headingStack.map((item) => item.text),
          }),
        );
        continue;
      }
      if (isTableLine(line)) {
        flush();
        const tableLines: string[] = [];
        while (index < lines.length && isTableLine(lines[index]!)) {
          tableLines.push(lines[index]!);
          index += 1;
        }
        index -= 1;
        const headerLine = tableLines[0]!.trim();
        const separator = tableLines[1] && /^\s*\|?\s*:?-{3,}/.test(tableLines[1]) ? tableLines[1]!.trim() : '';
        const headerText = separator ? `${headerLine}\n${separator}` : headerLine;
        const dataRows = tableLines.slice(separator ? 2 : 1);
        const tableHeadingPath = headingStack.map((item) => item.text);
        if (dataRows.length === 0) {
          paragraph += 1;
          elements.push(
            element(`el-${order + 1}`, 'paragraph', headerText, order++, {
              paragraph,
              headingPath: tableHeadingPath,
            }),
          );
        } else {
          dataRows.forEach((row, rowIndex) => {
            paragraph += 1;
            elements.push(
              element(`el-${order + 1}`, 'table_row', row.trim(), order++, {
                row: rowIndex + 1,
                paragraph,
                headingPath: tableHeadingPath,
                tableHeader: headerText,
              }),
            );
          });
        }
      } else if (!line.trim()) {
        flush();
      } else {
        if (buffer.length === 0) paragraphStart = paragraph + 1;
        buffer.push(line);
      }
    }
    flush();
  } else if (kind === 'csv') {
    const lines = normalized.split('\n').filter((line) => line.trim().length > 0);
    const header = lines[0]?.trim() ?? '';
    if (lines.length <= 1) {
      if (header) {
        elements.push(
          element(`el-${order + 1}`, 'paragraph', header, order++, {
            row: 1,
            paragraph: 1,
          }),
        );
        paragraph = 1;
      }
    } else {
      lines.slice(1).forEach((line, index) => {
        paragraph += 1;
        elements.push(
          element(`el-${order + 1}`, 'table_row', line.trim(), order++, {
            row: index + 1,
            paragraph,
            tableHeader: header,
          }),
        );
      });
    }
  } else {
    const chunks = normalized.split(/\n{2,}/);
    chunks.forEach((chunk) => {
      const text = chunk.trim();
      if (!text) return;
      paragraph += 1;
      elements.push(
        element(`el-${order + 1}`, 'paragraph', text, order++, {
          paragraph,
        }),
      );
    });
  }

  return {
    schemaVersion: 1,
    text: normalized,
    elements,
    engine: kind === 'markdown' ? 'local-markdown' : kind === 'csv' ? 'local-csv' : 'local-text',
    warnings: [],
  };
}

function validateRemoteResult(payload: unknown, fileName: string): ParseResult {
  if (!payload || typeof payload !== 'object') throw new ParserError('解析服务返回了无效 JSON');
  const data = payload as Record<string, unknown>;
  if (data.schemaVersion !== 1) throw new ParserError('解析服务响应 schemaVersion 不是 1');
  const text = typeof data.text === 'string' ? data.text : '';
  const rawElements = Array.isArray(data.elements) ? data.elements : [];
  const elements: ParsedElement[] = [];
  rawElements.forEach((item, index) => {
    if (!item || typeof item !== 'object') return;
    const value = item as Record<string, unknown>;
    if (typeof value.text !== 'string') return;
    const headingPath = Array.isArray(value.headingPath)
      ? value.headingPath.filter((part): part is string => typeof part === 'string')
      : undefined;
    elements.push({
      id: typeof value.id === 'string' ? value.id : `el-${index + 1}`,
      type: typeof value.type === 'string' ? value.type : 'paragraph',
      text: value.text,
      level: typeof value.level === 'number' ? value.level : undefined,
      page: typeof value.page === 'number' ? value.page : undefined,
      sheet: typeof value.sheet === 'string' ? value.sheet : undefined,
      row: typeof value.row === 'number' ? value.row : undefined,
      paragraph: typeof value.paragraph === 'number' ? value.paragraph : undefined,
      headingPath,
      tableHeader: typeof value.tableHeader === 'string' ? value.tableHeader : undefined,
      order: typeof value.order === 'number' ? value.order : index,
    });
  });
  const warnings = Array.isArray(data.warnings)
    ? data.warnings.filter((warning): warning is string => typeof warning === 'string')
    : [];
  if (!text.trim() && elements.length === 0) {
    throw new ParserError(`${fileName} 未提取到文字；扫描件需要 OCR/VLM，当前解析服务不支持`);
  }
  return {
    schemaVersion: 1,
    text: text || elements.map((item) => item.text).join('\n'),
    elements: elements.sort((a, b) => a.order - b.order),
    engine: typeof data.engine === 'string' ? data.engine : 'remote-parser',
    warnings,
  };
}

export async function parseDocument(input: ParseInput, options: ParserOptions): Promise<ParseResult> {
  const kind = textualKind(input.fileName);
  if (kind) {
    const text = input.text ?? input.buffer?.toString('utf8') ?? '';
    return parseLocalText(input.fileName, text, kind);
  }

  const dataBase64 =
    input.text !== undefined
      ? undefined
      : (input.buffer ?? Buffer.alloc(0)).toString('base64');
  if (!dataBase64 && input.text === undefined) throw new ParserError('待解析文件内容为空');

  let response: Response;
  try {
    response = await fetch(`${options.parserUrl.replace(/\/+$/, '')}/v1/parse`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(options.parserToken ? { 'x-parser-token': options.parserToken } : {}),
      },
      body: JSON.stringify({
        fileName: input.fileName,
        mime: input.mime,
        ...(dataBase64 ? { dataBase64 } : { text: input.text }),
      }),
      signal: AbortSignal.timeout(options.timeoutMs),
    });
  } catch (error) {
    throw new ParserError(
      redactSecrets(`解析服务不可用（${options.parserUrl}）：${error instanceof Error ? error.message : String(error)}`, [options.parserToken]),
    );
  }
  if (!response.ok) {
    let detail = '';
    try {
      const payload = (await response.json()) as { error?: unknown; message?: unknown };
      detail = typeof payload.message === 'string' ? payload.message : String(payload.error ?? '');
    } catch {
      detail = await response.text().catch(() => '');
    }
    throw new ParserError(redactSecrets(`解析服务返回 ${response.status}${detail ? `：${detail}` : ''}`, [options.parserToken]));
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ParserError('解析服务返回的不是 JSON');
  }
  return validateRemoteResult(payload, input.fileName);
}
