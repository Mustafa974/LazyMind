import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const workflowApi = vi.hoisted(() => ({ patchSlotItem: vi.fn() }));

vi.mock('@/modules/chat/utils/request', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/modules/chat/utils/request')>(),
  WorkflowSessionApi: () => workflowApi,
}));

import {
  ArtifactRewriteDialog,
  ArtifactRewriteInlineDiff,
  type ArtifactRewriteSelection,
} from './ArtifactRewriteDialog';

vi.mock('react-i18next', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-i18next')>();
  const translate = (key: string) => key;
  return {
    ...actual,
    useTranslation: () => ({ t: translate }),
  };
});

const selection: ArtifactRewriteSelection = {
  type: 'markdown',
  selected_text: 'Selected text',
  selectedText: 'Selected text',
  anchor: { top: 120, left: 240, placement: 'above' },
};

function renderDialog(requestPreview = vi.fn()) {
  render(
    <ArtifactRewriteDialog
      open
      sessionId='session-1'
      slotId='draft_document'
      listIndex={0}
      baseRevision={1}
      selection={selection}
      onClose={vi.fn()}
      onApplied={vi.fn()}
      requestPreview={requestPreview}
    />,
  );
  return requestPreview;
}

describe('ArtifactRewriteDialog', () => {
  beforeEach(() => workflowApi.patchSlotItem.mockReset());

  it('does not submit an empty or whitespace-only instruction', () => {
    const requestPreview = renderDialog();
    const input = screen.getByRole('textbox');
    const submit = screen.getByRole('button', { name: 'chat.artifactRewrite.preview' });

    expect(submit).toBeDisabled();
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(requestPreview).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: '   ' } });
    expect(submit).toBeDisabled();
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(requestPreview).not.toHaveBeenCalled();
  });

  it('submits a trimmed non-empty instruction', async () => {
    const requestPreview = renderDialog(vi.fn().mockResolvedValue({
      status: 'ready',
      action: 'rewrite_selection',
      base_revision: 1,
      representation: 'markdown',
      target: { type: 'block', block_type: 'paragraph' },
      preview: { old_text: 'Selected text', new_text: 'Rewritten text' },
      patch: { type: 'string_replace_set', payload: {} },
      artifact: { content_type: 'text/markdown', value: 'Rewritten text' },
    }));
    const input = screen.getByRole('textbox');
    const submit = screen.getByRole('button', { name: 'chat.artifactRewrite.preview' });

    fireEvent.change(input, { target: { value: '  Make it clearer  ' } });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    await waitFor(() => {
      expect(requestPreview).toHaveBeenCalledWith('Make it clearer', selection);
    });
  });

  it('returns both revision baselines after applying a preview', async () => {
    workflowApi.patchSlotItem.mockResolvedValue({
      data: {
        code: 0,
        data: { type: 'slot_item_patched', revision: 3, draft_version: 7 },
      },
    });
    const target = document.createElement('p');
    target.textContent = 'Selected text';
    const layer = document.createElement('div');
    document.body.append(target, layer);
    const onApplied = vi.fn();

    render(
      <ArtifactRewriteInlineDiff
        target={target}
        layer={layer}
        sessionId='session-1'
        slotId='draft_document'
        listIndex={-1}
        preview={{
          status: 'ready',
          action: 'rewrite_selection',
          base_revision: 3,
          base_draft_version: 6,
          representation: 'markdown',
          target: { type: 'block', block_type: 'paragraph' },
          preview: { old_text: 'Selected text', new_text: 'Rewritten text' },
          patch: { type: 'string_replace_set', payload: {} },
          artifact: { content_type: 'text/markdown', value: 'Rewritten text' },
        }}
        onApplied={onApplied}
        onReject={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'chat.artifactRewrite.apply' }));
    await waitFor(() => expect(onApplied).toHaveBeenCalledWith(3, 7));
  });
});
