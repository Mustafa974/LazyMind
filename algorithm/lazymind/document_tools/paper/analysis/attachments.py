"""Bind a complete F4 input from conversation attachments without LLM transcription."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

import lazyllm

from .schema import ContractError, normalize_input

# Same source-file ceiling as the conversation text-resource reader.
MAX_SOURCE_BYTES = 100 * 1024 * 1024


def _json_files(paths):
    return list(dict.fromkeys(
        path for path in paths if isinstance(path, str) and Path(urlsplit(path).path).suffix.lower() == '.json'
    ))


def _select_attachment(config, request):
    history = config.get('history_files_per_turn') or {}
    turns = sorted((key for key in history if str(key).isdigit()), key=int, reverse=True)
    groups = [history[key] for key in turns]
    groups.append(config.get('files') or [])
    groups = [_json_files(paths) for paths in groups]
    named = [[path for path in paths if Path(urlsplit(path).path).name in request] for paths in groups]
    candidates = next((paths for paths in named if paths), []) or next((paths for paths in groups if paths), [])
    if len(candidates) > 1:
        names = '、'.join(Path(urlsplit(path).path).name for path in candidates)
        raise ContractError('AMBIGUOUS_INPUT', 'input.paper_input',
                            f'本轮有多个 JSON 附件（{names}），请明确指定文件名或绑定 paper_input')
    return candidates[0] if candidates else None


def _unique_fields(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f'JSON 字段重复：{key}')
        value[key] = item
    return value


def _invalid_constant(value):
    raise ValueError(f'JSON 不允许 {value}')


def bind_attachment(bound: dict, workspace: str, request: str) -> dict:
    """Explicit inputs and confirmed pools take precedence over uploaded files.

    Candidates come only from the host's current conversation attachment catalog.
    Resolve through that catalog, read the complete bytes, validate the F4 spec,
    then freeze a private input before the ordinary Runtime adapter consumes it.
    """
    if 'paper_input' in bound or any(key.endswith('_pool') for key in bound):
        return bound
    config = lazyllm.globals.get('agentic_config') or {}
    source = _select_attachment(config, request)
    if source is None:
        return bound
    from lazymind.chat.engine.tools.file_resources.resolver import resolve_text_target
    from lazymind.chat.engine.tools.host_file_resolution import open_input_file

    filename = Path(urlsplit(source).path).name
    try:
        resolved = resolve_text_target(source, resources_only=True)
        if resolved.kind != 'attachment_text':
            raise ValueError('目标不是当前会话的 JSON 文本附件')
        with open_input_file(resolved.path) as stream:
            raw = stream.read(MAX_SOURCE_BYTES + 1)
    except (OSError, ValueError) as exc:
        raise ContractError('MISSING_MATERIAL', 'input.paper_input',
                            f'无法读取已上传的 JSON 附件 {filename}，请检查文件后重试') from exc
    if len(raw) > MAX_SOURCE_BYTES:
        raise ContractError('INPUT_TOO_LARGE', 'input.paper_input', 'JSON 附件超过 100 MiB，请缩小输入')
    try:
        value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_fields,
                           parse_constant=_invalid_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ContractError('INVALID_INPUT', 'input.paper_input',
                            f'附件 {filename} 不是有效的 UTF-8 JSON：{exc}') from exc
    value = normalize_input(value)
    directory = Path(tempfile.mkdtemp(prefix='f4-input-', dir=workspace))
    target = directory / 'paper_input.json'
    target.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    return {**bound, 'paper_input': str(target)}
