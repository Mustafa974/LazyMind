from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

DRAFT_STREAM_EVENT_TYPES = frozenset({
    'artifact_stream_start',
    'artifact_stream',
    'artifact_stream_end',
    'artifact_stream_abort',
})

SUBAGENT_SSE_HEARTBEAT_INTERVAL = 2.0
SUBAGENT_SSE_HEARTBEAT = ': heartbeat\n\n'


async def with_idle_sse_heartbeats(
    events: AsyncIterator[str],
    heartbeat_interval: float = SUBAGENT_SSE_HEARTBEAT_INTERVAL,
) -> AsyncIterator[str]:
    """Emit an SSE comment only when the upstream has been idle."""
    iterator = events.__aiter__()
    next_event: asyncio.Task[Any] | None = asyncio.create_task(iterator.__anext__())
    timeout = heartbeat_interval if heartbeat_interval > 0 else None
    try:
        while next_event is not None:
            done, _ = await asyncio.wait(
                {next_event},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                # Prefer a business event that became ready at the timeout boundary.
                await asyncio.sleep(0)
                if not next_event.done():
                    yield SUBAGENT_SSE_HEARTBEAT
                    continue

            try:
                event = next_event.result()
            except StopAsyncIteration:
                next_event = None
                continue
            yield event
            next_event = asyncio.create_task(iterator.__anext__())
    finally:
        if next_event is not None and not next_event.done():
            next_event.cancel()
            try:
                await next_event
            except (asyncio.CancelledError, StopAsyncIteration):
                pass


async def merge_agent_and_stream_events(
    agent_events: AsyncIterator[Any],
    stream_events: asyncio.Queue[dict[str, Any]],
) -> AsyncIterator[tuple[str, Any]]:
    """Yield tool-thread stream events while the Agent iterator is still running."""
    iterator = agent_events.__aiter__()
    agent_task: asyncio.Task[Any] | None = asyncio.create_task(iterator.__anext__())
    stream_task: asyncio.Task[Any] | None = asyncio.create_task(stream_events.get())
    agent_error: BaseException | None = None
    try:
        while agent_task is not None:
            wait_for = {agent_task}
            if stream_task is not None:
                wait_for.add(stream_task)
            done, _ = await asyncio.wait(wait_for, return_when=asyncio.FIRST_COMPLETED)

            if stream_task is not None and stream_task in done:
                yield 'stream', stream_task.result()
                stream_task = asyncio.create_task(stream_events.get())

            if agent_task in done:
                try:
                    item = agent_task.result()
                except StopAsyncIteration:
                    agent_task = None
                except BaseException as exc:  # noqa: BLE001 - preserve cancellation and failure.
                    agent_error = exc
                    agent_task = None
                else:
                    yield 'agent', item
                    agent_task = asyncio.create_task(iterator.__anext__())

        # Deliver call_soon_threadsafe callbacks queued immediately before the
        # tool/agent future completed, then propagate the original exception.
        await asyncio.sleep(0)
        if stream_task is not None and stream_task.done():
            yield 'stream', stream_task.result()
            stream_task = None
        while not stream_events.empty():
            yield 'stream', stream_events.get_nowait()
        if agent_error is not None:
            raise agent_error
    finally:
        for pending in (agent_task, stream_task):
            if pending is not None and not pending.done():
                pending.cancel()


__all__ = [
    'DRAFT_STREAM_EVENT_TYPES',
    'SUBAGENT_SSE_HEARTBEAT',
    'SUBAGENT_SSE_HEARTBEAT_INTERVAL',
    'merge_agent_and_stream_events',
    'with_idle_sse_heartbeats',
]
