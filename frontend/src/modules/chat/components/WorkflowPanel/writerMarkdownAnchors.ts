const MATERIALIZED_SYSTEM_ANCHOR_RE = /<a\s+id=(["'])(block-[^"']+)\1\s*>\s*<\/a>/gi;
const EDITOR_SYSTEM_ANCHOR_RE = /<a\s+id=(["'])(block-[^"']+)\1\s*\/>/gi;

export interface WriterMarkdownReferenceTarget {
  anchorId: string;
  label: string;
}

/** MDXEditor preserves empty anchors as JSX, whose canonical form is self-closing. */
export function writerMarkdownForEditor(markdown: string): string {
  return markdown.replace(
    MATERIALIZED_SYSTEM_ANCHOR_RE,
    (_match, _quote: string, anchorId: string) => `<a id="${anchorId}" />`,
  );
}

/** The Writer numbering service consumes paired system anchors. */
export function writerMarkdownForSave(markdown: string): string {
  return markdown.replace(
    EDITOR_SYSTEM_ANCHOR_RE,
    (_match, _quote: string, anchorId: string) => `<a id="${anchorId}"></a>`,
  );
}

/** Collect system anchors and the first heading immediately following each anchor. */
export function collectWriterMarkdownReferenceTargets(
  markdown: string,
): WriterMarkdownReferenceTarget[] {
  const anchorPattern = /<a\s+id=(["'])(block-[^"']+)\1\s*(?:\/>|>\s*<\/a>)/gi;
  return Array.from(markdown.matchAll(anchorPattern)).map((match) => {
    const anchorId = match[2];
    const trailing = markdown.slice((match.index ?? 0) + match[0].length);
    const firstContentLine = trailing
      .split(/\r?\n/)
      .map((line) => line.trim())
      .find(Boolean);
    const heading = firstContentLine?.match(/^#{1,6}\s+(.+?)\s*$/);
    return {
      anchorId,
      label: heading?.[1]?.trim() || anchorId,
    };
  });
}

export function writerMarkdownInternalReference(text: string, anchorId: string): string {
  const label = text
    .replace(/\\/g, '\\\\')
    .replace(/\]/g, '\\]')
    .replace(/\s+/g, ' ')
    .trim();
  return label && anchorId.startsWith('block-')
    ? `[${label}](#${anchorId})`
    : '';
}

/** Replace the selected source text in-place so its visible wording stays unchanged. */
export function applyWriterMarkdownInternalReference(
  markdown: string,
  paragraphText: string,
  startOffset: number,
  selectedText: string,
  anchorId: string,
): string {
  const reference = writerMarkdownInternalReference(selectedText, anchorId);
  if (!reference || !paragraphText || startOffset < 0) return markdown;

  const matches: number[] = [];
  const blockPattern = /(?:^|\n{2,})([^\n][\s\S]*?)(?=\n{2,}|$)/g;
  for (const blockMatch of markdown.matchAll(blockPattern)) {
    const block = blockMatch[1];
    const blockStart = (blockMatch.index ?? 0) + blockMatch[0].length - block.length;
    let visibleText = '';
    const sourceOffsets: number[] = [];
    for (let index = 0; index < block.length;) {
      const link = block.slice(index).match(/^\[([^\]]*)\]\([^)]+\)/);
      if (link) {
        visibleText += link[1];
        for (let labelIndex = 0; labelIndex < link[1].length; labelIndex += 1) {
          sourceOffsets.push(index + 1 + labelIndex);
        }
        index += link[0].length;
        continue;
      }
      visibleText += block[index];
      sourceOffsets.push(index);
      index += 1;
    }
    if (visibleText !== paragraphText) continue;
    const sourceOffset = sourceOffsets[startOffset];
    if (sourceOffset === undefined) continue;
    const selectionStart = blockStart + sourceOffset;
    if (markdown.slice(selectionStart, selectionStart + selectedText.length) === selectedText) {
      matches.push(selectionStart);
    }
  }
  if (matches.length !== 1) return markdown;

  const selectionStart = matches[0];
  return `${markdown.slice(0, selectionStart)}${reference}${markdown.slice(selectionStart + selectedText.length)}`;
}

/** Keep user-authored Markdown link labels after the server materializes numbering. */
export function restoreWriterMarkdownInternalReferenceLabels(
  materializedMarkdown: string,
  sourceMarkdown: string,
): string {
  const referencePattern = /\[([^\]]*)\]\(#(block-[^)]+)\)/g;
  const sourceLabels = new Map<string, string[]>();
  for (const match of sourceMarkdown.matchAll(referencePattern)) {
    const labels = sourceLabels.get(match[2]) ?? [];
    labels.push(match[1]);
    sourceLabels.set(match[2], labels);
  }

  return materializedMarkdown.replace(referencePattern, (reference, _label: string, anchorId: string) => {
    const labels = sourceLabels.get(anchorId);
    const sourceLabel = labels?.shift();
    return sourceLabel ? `[${sourceLabel}](#${anchorId})` : reference;
  });
}
