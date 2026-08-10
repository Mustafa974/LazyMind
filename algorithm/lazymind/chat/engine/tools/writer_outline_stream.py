"""Streaming-only adapters for user-visible Writer outline generation."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from threading import RLock
from typing import Any, ClassVar

from lazyllm import LOG, AutoModel
from lazyllm.tools.writer.data_models import WriterDocument
from lazyllm.tools.writer.tools.outline_stream_tools import WriterOutlineStreamingTools

from .writer import (
    _json_loads,
    _primary_data,
    _temp_root,
    _write_input_artifact,
    writer_schema,
)


class WriterArtifactStreamEventEmitter:
    """Publish one attempt-scoped Markdown preview for an artifact slot."""

    EVENT_TYPES: ClassVar[dict[str, str]] = {
        "start": "artifact_stream_start",
        "delta": "artifact_stream",
        "end": "artifact_stream_end",
        "abort": "artifact_stream_abort",
    }

    def __init__(
        self,
        emit: Callable[[dict[str, Any]], None],
        *,
        slot: str,
    ) -> None:
        if not slot.strip():
            raise ValueError("slot must not be empty.")
        self._emit = emit
        self._slot = slot.strip()
        self._stream_id = uuid.uuid4().hex
        self._chunk_index = 0
        self._closed = False
        self._lock = RLock()
        with self._lock:
            self._publish_locked("start")

    @property
    def stream_id(self) -> str:
        return self._stream_id

    def feed(self, delta: str) -> None:
        if not delta:
            return
        with self._lock:
            if self._closed:
                return
            self._publish_locked("delta", delta=delta)

    def end(self) -> None:
        self._finish("end")

    def abort(self, message: str = "") -> None:
        self._finish("abort", message=message)

    def _finish(self, event: str, *, message: str = "") -> None:
        with self._lock:
            if self._closed:
                return
            self._publish_locked(event, message=message)
            self._closed = True

    def _publish_locked(
        self, event: str, *, delta: str = "", message: str = ""
    ) -> None:
        self._chunk_index += 1
        payload: dict[str, Any] = {
            "type": self.EVENT_TYPES[event],
            "slot": self._slot,
            "content_type": "text/markdown",
            "stream_id": self._stream_id,
            "chunk_index": self._chunk_index,
        }
        if event == "delta":
            payload["delta"] = delta
        elif event == "abort" and message:
            payload["message"] = message
        try:
            self._emit(payload)
        except Exception as exc:  # noqa: BLE001 - preview forwarding is best effort.
            LOG.warning("[Writer] failed to forward outline stream event: %s", exc)


def stream_writer_outline(
    writing_task_json: str,
    writing_context_json: str,
    on_delta: Callable[[str], None],
) -> str:
    """Generate an outline and return its final representation after streaming."""
    root = _temp_root()
    task_path = _write_input_artifact(
        root,
        "writing_task.json",
        _json_loads(writing_task_json, {}),
        writer_schema("task.WritingTask"),
    )
    context_path = _write_input_artifact(
        root,
        "writing_context.json",
        _json_loads(writing_context_json, {}),
        writer_schema("context.WritingContext"),
    )
    planning = WriterOutlineStreamingTools(
        llm=AutoModel(model="llm"),
        artifact_store=str(root),
    )
    with planning.stream_outline(task=task_path, context=context_path) as stream:
        for delta in stream:
            try:
                on_delta(delta)
            except Exception as exc:  # noqa: BLE001 - preview forwarding is best effort.
                LOG.warning("[Writer] Outline delta callback failed: %s", exc)
        result = stream.result()

    outline = _primary_data(result)
    if isinstance(outline, str):
        return outline
    return WriterDocument.model_validate(outline).model_dump_json(exclude_defaults=True)


__all__ = ["WriterArtifactStreamEventEmitter", "stream_writer_outline"]
