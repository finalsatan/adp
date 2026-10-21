import { Fragment, type ReactNode } from 'react';
import type { Citation } from '../api/types';

interface RichTextProps {
  content: string;
  citations?: Citation[];
  onCitation?: (citation: Citation) => void;
  compact?: boolean;
}

const INLINE_PATTERN = /(\[\d{1,2}\])|(\*\*[^*]+\*\*)|(`[^`]+`)/g;

function renderInline(
  text: string,
  citations: Citation[] | undefined,
  onCitation: ((citation: Citation) => void) | undefined,
): ReactNode[] {
  const nodes: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  const pattern = new RegExp(INLINE_PATTERN.source, 'g');
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) nodes.push(text.slice(lastIndex, match.index));
    const token = match[0];
    if (token.startsWith('[') && token.endsWith(']')) {
      const index = Number(token.slice(1, -1));
      const citation = citations?.find((item) => item.index === index);
      if (citation) {
        nodes.push(
          <button
            key={`${match.index}-citation`}
            type="button"
            className="citation-ref"
            onClick={() => onCitation?.(citation)}
            title={`${citation.documentName} · 点击查看引用原文`}
          >
            {index}
          </button>,
        );
      } else {
        nodes.push(
          <span key={`${match.index}-plain`} className="citation-ref is-plain">
            {index}
          </span>,
        );
      }
    } else if (token.startsWith('**')) {
      nodes.push(<strong key={`${match.index}-bold`}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith('`')) {
      nodes.push(
        <code key={`${match.index}-code`} className="inline-code">
          {token.slice(1, -1)}
        </code>,
      );
    }
    lastIndex = match.index + token.length;
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex));
  return nodes;
}

function splitTableRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim());
}

/** Lightweight Markdown renderer: headings, lists, quotes, tables, code and citations. */
export function RichText({ content, citations, onCitation, compact = false }: RichTextProps) {
  const lines = (content ?? '').replace(/\r\n/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];

    if (line.startsWith('```')) {
      const language = line.slice(3).trim();
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].startsWith('```')) {
        code.push(lines[index]);
        index += 1;
      }
      index += 1;
      blocks.push(
        <pre key={`code-${index}`} className="rich-code" data-language={language || undefined}>
          <code>{code.join('\n')}</code>
        </pre>,
      );
      continue;
    }

    if (!line.trim()) {
      index += 1;
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      const level = heading[1].length;
      const text = heading[2];
      blocks.push(
        <div key={`heading-${index}`} className={`rich-heading rich-h${level}`}>
          {renderInline(text, citations, onCitation)}
        </div>,
      );
      index += 1;
      continue;
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*[-*]\s+/, ''));
        index += 1;
      }
      blocks.push(
        <ul key={`ul-${index}`} className="rich-list">
          {items.map((item, itemIndex) => (
            <li key={itemIndex}>{renderInline(item, citations, onCitation)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*\d+[.)]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*\d+[.)]\s+/, ''));
        index += 1;
      }
      blocks.push(
        <ol key={`ol-${index}`} className="rich-list">
          {items.map((item, itemIndex) => (
            <li key={itemIndex}>{renderInline(item, citations, onCitation)}</li>
          ))}
        </ol>,
      );
      continue;
    }

    if (line.startsWith('>')) {
      const quote: string[] = [];
      while (index < lines.length && lines[index].startsWith('>')) {
        quote.push(lines[index].replace(/^>\s?/, ''));
        index += 1;
      }
      blocks.push(
        <blockquote key={`quote-${index}`} className="rich-quote">
          {renderInline(quote.join('\n'), citations, onCitation)}
        </blockquote>,
      );
      continue;
    }

    if (line.trim().startsWith('|') && lines[index + 1]?.includes('---')) {
      const header = splitTableRow(line);
      index += 2;
      const rows: string[][] = [];
      while (index < lines.length && lines[index].trim().startsWith('|')) {
        rows.push(splitTableRow(lines[index]));
        index += 1;
      }
      blocks.push(
        <div key={`table-${index}`} className="rich-table-wrap">
          <table className="rich-table">
            <thead>
              <tr>
                {header.map((cell, cellIndex) => (
                  <th key={cellIndex}>{renderInline(cell, citations, onCitation)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex}>{renderInline(cell, citations, onCitation)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    const paragraph: string[] = [line];
    index += 1;
    while (
      index < lines.length &&
      lines[index].trim() &&
      !lines[index].startsWith('```') &&
      !/^(#{1,4})\s+/.test(lines[index]) &&
      !/^\s*[-*]\s+/.test(lines[index]) &&
      !/^\s*\d+[.)]\s+/.test(lines[index]) &&
      !lines[index].startsWith('>') &&
      !lines[index].trim().startsWith('|')
    ) {
      paragraph.push(lines[index]);
      index += 1;
    }
    blocks.push(
      <p key={`p-${index}`} className={compact ? 'rich-p is-compact' : 'rich-p'}>
        {paragraph.map((part, partIndex) => (
          <Fragment key={partIndex}>
            {partIndex > 0 && <br />}
            {renderInline(part, citations, onCitation)}
          </Fragment>
        ))}
      </p>,
    );
  }

  if (blocks.length === 0) {
    return <p className="rich-p rich-empty">（空内容）</p>;
  }

  return <div className={`rich-text${compact ? ' rich-compact' : ''}`}>{blocks}</div>;
}
