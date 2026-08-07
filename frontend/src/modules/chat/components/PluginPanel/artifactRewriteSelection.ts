export interface SelectionActionAnchor {
  top: number;
  left: number;
  placement: 'above' | 'below';
}

export interface MarkdownSelection {
  text: string;
  anchor: SelectionActionAnchor;
  supported: boolean;
  paragraph?: HTMLElement;
  startOffset?: number;
}

function closestElement(node: Node | null): HTMLElement | null {
  return node instanceof HTMLElement ? node : node?.parentElement ?? null;
}

export function selectionActionAnchor(range: Range): SelectionActionAnchor | null {
  const rect = range.getBoundingClientRect();
  if (rect.width === 0 && rect.height === 0) return null;

  const placement = rect.top >= 48 ? 'above' : 'below';
  const edge = Math.min(56, window.innerWidth / 2);
  return {
    top: placement === 'above'
      ? Math.max(8, rect.top - 8)
      : Math.min(window.innerHeight - 40, rect.bottom + 8),
    left: Math.min(Math.max(edge, rect.left + rect.width / 2), window.innerWidth - edge),
    placement,
  };
}

/**
 * Captures the visible selection and whether it stays inside one ordinary
 * Markdown paragraph. The server remains the source of truth for matching it.
 */
export function selectedMarkdownParagraph(container: HTMLElement): MarkdownSelection | null {
  const selection = globalThis.getSelection();
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return null;

  const range = selection.getRangeAt(0);
  if (!container.contains(range.startContainer) || !container.contains(range.endContainer)) {
    return null;
  }

  const startParagraph = closestElement(range.startContainer)?.closest('p');
  const endParagraph = closestElement(range.endContainer)?.closest('p');
  const selectedText = selection.toString();
  const text = selectedText.trim();
  const anchor = selectionActionAnchor(range);
  if (!text || !anchor) return null;

  const supported = Boolean(
    startParagraph
      && startParagraph === endParagraph
      && container.contains(startParagraph)
      && !startParagraph.closest('li, blockquote, pre, td, th'),
  );
  let startOffset: number | undefined;
  if (supported && startParagraph) {
    const prefixRange = range.cloneRange();
    prefixRange.selectNodeContents(startParagraph);
    prefixRange.setEnd(range.startContainer, range.startOffset);
    startOffset = prefixRange.toString().length + selectedText.length - selectedText.trimStart().length;
  }
  return { text, anchor, supported, paragraph: startParagraph ?? undefined, startOffset };
}
