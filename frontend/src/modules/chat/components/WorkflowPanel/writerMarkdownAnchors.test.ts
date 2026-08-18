import { describe, expect, it } from 'vitest';
import { isWriterSystemAnchorBlock } from './writerIR';
import {
  collectWriterMarkdownReferenceTargets,
  writerMarkdownForEditor,
  writerMarkdownForSave,
  writerMarkdownInternalReference,
} from './writerMarkdownAnchors';

describe('Writer Markdown system anchors', () => {
  it('keeps system anchors round-trippable through MDXEditor', () => {
    const source = '# 标题\n\n<a id="block-sec-1"></a>\n## 1 章节';
    const editorValue = writerMarkdownForEditor(source);

    expect(editorValue).toContain('<a id="block-sec-1" />');
    expect(writerMarkdownForSave(editorValue)).toBe(source);
  });

  it('does not rewrite unrelated HTML anchors', () => {
    const source = '<a id="custom"></a>正文';

    expect(writerMarkdownForEditor(source)).toBe(source);
    expect(writerMarkdownForSave(source)).toBe(source);
  });

  it('identifies only IR paragraphs that contain a system anchor', () => {
    expect(isWriterSystemAnchorBlock({
      node_id: 'anchor-1',
      type: 'paragraph',
      content: '<a id="block-sec-1" />',
    })).toBe(true);
    expect(isWriterSystemAnchorBlock({
      node_id: 'paragraph-1',
      type: 'paragraph',
      content: '<a id="custom"></a>',
    })).toBe(false);
  });

  it('collects paired and editor-form anchors with their heading labels', () => {
    const source = [
      '<a id="block-sec-1"></a>',
      '## 1 系统设计',
      '',
      '<a id="block-sec-2" />',
      '### 1.1 接口设计',
    ].join('\n');

    expect(collectWriterMarkdownReferenceTargets(source)).toEqual([
      { anchorId: 'block-sec-1', label: '1 系统设计' },
      { anchorId: 'block-sec-2', label: '1.1 接口设计' },
    ]);
  });

  it('constructs an internal Markdown link from selected text and an anchor', () => {
    expect(writerMarkdownInternalReference('第 1 节', 'block-sec-1'))
      .toBe('[第 1 节](#block-sec-1)');
  });
});
