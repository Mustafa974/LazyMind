import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react';
import ReactDOM from 'react-dom';
import { useTranslation } from 'react-i18next';
import {
  PluginSessionApi,
  type RewriteSelection,
  type RewriteSelectionPreview,
} from '@/modules/chat/utils/request';
import './ArtifactRewriteDialog.scss';

export type ArtifactRewriteSelection = RewriteSelection & { selectedText: string };

interface ArtifactRewriteDialogProps {
  open: boolean;
  sessionId: string;
  slotId: string;
  listIndex: number;
  baseRevision: number;
  selection: ArtifactRewriteSelection | null;
  onClose: () => void;
  onApplied: (revision?: number) => void;
}

type DialogPhase = 'form' | 'previewing' | 'ready' | 'applying';

function errorCode(error: unknown): string | undefined {
  if (!error || typeof error !== 'object') return undefined;
  const response = (error as {
    response?: {
      data?: {
        code?: unknown;
        error_code?: unknown;
        data?: { code?: unknown; error_code?: unknown };
      };
    };
  }).response;
  const data = response?.data;
  const code = data?.error_code ?? data?.code ?? data?.data?.error_code ?? data?.data?.code;
  return typeof code === 'string' ? code : undefined;
}

function errorMessage(code: string | undefined, fallback: string): string {
  if (code === 'REVISION_CONFLICT') return 'chat.artifactRewrite.errors.revisionConflict';
  if (code === 'SELECTION_AMBIGUOUS') return 'chat.artifactRewrite.errors.ambiguous';
  if (code === 'SELECTION_STALE') return 'chat.artifactRewrite.errors.stale';
  if (code === 'SELECTION_UNSUPPORTED') return 'chat.artifactRewrite.errors.unsupported';
  if (code === 'PLUGIN_ACTION_FAILED') return 'chat.artifactRewrite.errors.pluginFailed';
  return fallback;
}

function isReadyPreview(value: unknown): value is RewriteSelectionPreview {
  if (!value || typeof value !== 'object') return false;
  const data = value as RewriteSelectionPreview;
  return data.status === 'ready'
    && data.action === 'rewrite_selection'
    && typeof data.base_revision === 'number'
    && typeof data.preview?.old_text === 'string'
    && typeof data.preview?.new_text === 'string'
    && typeof data.artifact?.content_type === 'string'
    && Boolean(data.artifact.value && typeof data.artifact.value === 'object');
}

export function ArtifactRewriteDialog({
  open,
  sessionId,
  slotId,
  listIndex,
  baseRevision,
  selection,
  onClose,
  onApplied,
}: ArtifactRewriteDialogProps) {
  const { t } = useTranslation();
  const [instruction, setInstruction] = useState('');
  const [preview, setPreview] = useState<RewriteSelectionPreview | null>(null);
  const [phase, setPhase] = useState<DialogPhase>('form');
  const [error, setError] = useState<string>();
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const lastFocusRef = useRef<HTMLElement | null>(null);
  const mountedRef = useRef(true);
  const requestIdRef = useRef(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestIdRef.current += 1;
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    lastFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setInstruction(t('chat.artifactRewrite.defaultInstruction'));
    setPreview(null);
    setError(undefined);
    setPhase('form');
    const timer = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [open, selection, t]);

  const close = useCallback(() => {
    if (phase === 'previewing' || phase === 'applying') return;
    onClose();
    window.setTimeout(() => lastFocusRef.current?.focus(), 0);
  }, [onClose, phase]);

  const requestPreview = useCallback(async () => {
    const trimmedInstruction = instruction.trim();
    if (!selection || !trimmedInstruction || phase !== 'form') return;

    const requestId = ++requestIdRef.current;
    setPhase('previewing');
    setError(undefined);
    try {
      const response = await PluginSessionApi().previewRewriteSelection(
        sessionId,
        slotId,
        listIndex,
        {
          action: 'rewrite_selection',
          base_revision: baseRevision,
          input: {
            instruction: trimmedInstruction,
            selection: selection.type === 'ir'
              ? { type: 'ir', node_id: selection.node_id }
              : { type: 'markdown', selected_text: selection.selected_text },
          },
        },
        { silentError: true } as never,
      );
      const result = response?.data?.data;
      if (response?.data?.code !== 0 || !isReadyPreview(result)) {
        throw new Error('invalid preview response');
      }
      if (!mountedRef.current || requestId !== requestIdRef.current) return;
      setPreview(result);
      setPhase('ready');
    } catch (requestError) {
      if (!mountedRef.current || requestId !== requestIdRef.current) return;
      setError(t(errorMessage(errorCode(requestError), 'chat.artifactRewrite.errors.previewFailed')));
      setPhase('form');
    }
  }, [baseRevision, instruction, listIndex, phase, selection, sessionId, slotId, t]);

  const applyPreview = useCallback(async () => {
    if (!preview || phase !== 'ready') return;
    const requestId = ++requestIdRef.current;
    setPhase('applying');
    setError(undefined);
    try {
      const response = await PluginSessionApi().patchSlotItem(
        sessionId,
        slotId,
        listIndex,
        preview.artifact.value,
        preview.artifact.content_type,
        'checkpoint',
        undefined,
        { silentError: true } as never,
      );
      const result = response?.data?.data;
      if (response?.data?.code !== 0 || result?.type !== 'slot_item_patched') {
        throw new Error('invalid patch response');
      }
      if (!mountedRef.current || requestId !== requestIdRef.current) return;
      onApplied(typeof result.revision === 'number' ? result.revision : undefined);
      onClose();
      window.setTimeout(() => lastFocusRef.current?.focus(), 0);
    } catch (applyError) {
      if (!mountedRef.current || requestId !== requestIdRef.current) return;
      setError(t(errorMessage(errorCode(applyError), 'chat.artifactRewrite.errors.applyFailed')));
      setPhase('ready');
    }
  }, [listIndex, onApplied, onClose, phase, preview, sessionId, slotId, t]);

  const handleKeyDown = useCallback((event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
      'button:not(:disabled), textarea:not(:disabled)',
    ));
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }, [close]);

  if (!open || !selection) return null;
  const busy = phase === 'previewing' || phase === 'applying';
  const canPreview = phase === 'form' && instruction.trim().length > 0;

  return ReactDOM.createPortal(
    <div
      className='artifact-rewrite-dialog__overlay'
      role='presentation'
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div
        className='artifact-rewrite-dialog'
        role='dialog'
        aria-modal='true'
        aria-labelledby='artifact-rewrite-dialog-title'
        onKeyDown={handleKeyDown}
      >
        <header className='artifact-rewrite-dialog__header'>
          <h2 id='artifact-rewrite-dialog-title'>{t('chat.artifactRewrite.title')}</h2>
          <button type='button' onClick={close} disabled={busy} aria-label={t('chat.artifactRewrite.close')}>×</button>
        </header>

        <div className='artifact-rewrite-dialog__body'>
          <p className='artifact-rewrite-dialog__selection'>
            {t('chat.artifactRewrite.selectedText')}: {selection.selectedText}
          </p>
          {preview ? (
            <div className='artifact-rewrite-dialog__diff' aria-live='polite'>
              <div>
                <h3>{t('chat.artifactRewrite.before')}</h3>
                <pre>{preview.preview.old_text}</pre>
              </div>
              <div>
                <h3>{t('chat.artifactRewrite.after')}</h3>
                <pre>{preview.preview.new_text}</pre>
              </div>
            </div>
          ) : (
            <label className='artifact-rewrite-dialog__instruction'>
              <span>{t('chat.artifactRewrite.instruction')}</span>
              <textarea
                ref={inputRef}
                value={instruction}
                onChange={(event) => setInstruction(event.target.value)}
                placeholder={t('chat.artifactRewrite.instructionPlaceholder')}
                disabled={busy}
                aria-describedby={error ? 'artifact-rewrite-dialog-error' : undefined}
              />
            </label>
          )}
          {error && <p id='artifact-rewrite-dialog-error' className='artifact-rewrite-dialog__error' role='alert'>{error}</p>}
        </div>

        <footer className='artifact-rewrite-dialog__footer'>
          <button type='button' onClick={close} disabled={busy}>{t('common.cancel')}</button>
          {preview ? (
            <button type='button' className='artifact-rewrite-dialog__primary' onClick={() => void applyPreview()} disabled={busy}>
              {phase === 'applying' ? t('chat.artifactRewrite.applying') : t('chat.artifactRewrite.apply')}
            </button>
          ) : (
            <button type='button' className='artifact-rewrite-dialog__primary' onClick={() => void requestPreview()} disabled={!canPreview || busy}>
              {phase === 'previewing' ? t('chat.artifactRewrite.previewing') : t('chat.artifactRewrite.preview')}
            </button>
          )}
        </footer>
      </div>
    </div>,
    document.body,
  );
}
