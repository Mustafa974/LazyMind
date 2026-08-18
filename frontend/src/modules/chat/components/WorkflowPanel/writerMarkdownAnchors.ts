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
