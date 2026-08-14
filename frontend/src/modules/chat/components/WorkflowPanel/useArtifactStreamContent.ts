import { useCallback, useLayoutEffect, useRef, useState } from 'react';

/**
 * Reveal exactly one backend delta per paint when several SSE frames arrive in
 * the same browser callback. Individual deltas are never split or rewritten.
 */
export function useArtifactStreamContent(
  streamId: string,
  deltas: string[] | undefined,
  fallbackContent: string,
): string {
  const [visibleContent, setVisibleContent] = useState(
    () => deltas?.[0] ?? fallbackContent,
  );
  const streamIdRef = useRef(streamId);
  const deltasRef = useRef(deltas);
  const displayedCountRef = useRef(deltas?.length ? 1 : 0);
  const frameRef = useRef<number>();

  const cancelFrame = useCallback(() => {
    if (frameRef.current === undefined) return;
    window.cancelAnimationFrame(frameRef.current);
    frameRef.current = undefined;
  }, []);

  const drainNext = useCallback(function drainNext() {
    frameRef.current = undefined;
    const currentDeltas = deltasRef.current;
    if (!currentDeltas) return;

    const index = displayedCountRef.current;
    if (index >= currentDeltas.length) return;
    displayedCountRef.current = index + 1;
    setVisibleContent((current) => current + currentDeltas[index]);

    if (displayedCountRef.current < currentDeltas.length) {
      frameRef.current = window.requestAnimationFrame(drainNext);
    }
  }, []);

  useLayoutEffect(() => {
    if (streamIdRef.current !== streamId) {
      cancelFrame();
      streamIdRef.current = streamId;
      displayedCountRef.current = 0;
      setVisibleContent('');
    }

    deltasRef.current = deltas;
    if (!deltas) {
      cancelFrame();
      displayedCountRef.current = 0;
      setVisibleContent(fallbackContent);
      return;
    }

    if (displayedCountRef.current > deltas.length) {
      displayedCountRef.current = 0;
      setVisibleContent('');
    }

    // Apply the first pending backend delta before the next paint. Any further
    // deltas from the same network batch are revealed on following frames.
    if (frameRef.current === undefined && displayedCountRef.current < deltas.length) {
      drainNext();
    }
  }, [cancelFrame, deltas, drainNext, fallbackContent, streamId]);

  useLayoutEffect(() => cancelFrame, [cancelFrame]);

  return visibleContent;
}
