import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useArtifactStreamContent } from './useArtifactStreamContent';

describe('useArtifactStreamContent', () => {
  let callbacks: FrameRequestCallback[];

  beforeEach(() => {
    callbacks = [];
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      callbacks.push(callback);
      return callbacks.length;
    });
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('reveals backend deltas in order without splitting them', () => {
    const { result, rerender } = renderHook(
      ({ deltas }) => useArtifactStreamContent('stream-1', deltas, deltas.join('')),
      { initialProps: { deltas: [] as string[] } },
    );

    rerender({ deltas: ['# 标题\n\n', '## 子标题\n\n', '正文'] });
    expect(result.current).toBe('# 标题\n\n');

    act(() => callbacks.shift()?.(16));
    expect(result.current).toBe('# 标题\n\n## 子标题\n\n');

    act(() => callbacks.shift()?.(32));
    expect(result.current).toBe('# 标题\n\n## 子标题\n\n正文');
  });

  it('shows legacy streams without delta metadata immediately', () => {
    const { result, rerender } = renderHook(
      ({ content }) => useArtifactStreamContent('legacy-stream', undefined, content),
      { initialProps: { content: '旧内容' } },
    );

    rerender({ content: '旧内容和新内容' });
    expect(result.current).toBe('旧内容和新内容');
    expect(callbacks).toHaveLength(0);
  });
});
