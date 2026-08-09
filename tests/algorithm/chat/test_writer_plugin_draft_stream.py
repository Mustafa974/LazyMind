"""Regression coverage for writer-plugin draft preview events."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


_ROOT = Path(__file__).resolve().parents[3]
_TOOLS_PATH = _ROOT / 'plugins' / 'writer-plugin' / 'scripts' / 'tools.py'


def _load_tools_module() -> ModuleType:
    module_name = 'writer_plugin_tools_draft_stream_test'
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, _TOOLS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_write_document_revision_emits_markdown_draft_stream(monkeypatch, tmp_path):
    tools = _load_tools_module()
    events: list[dict] = []
    context = SimpleNamespace(
        workspace_path=str(tmp_path),
        params={'step_id': 'write_document'},
        emit=events.append,
    )

    class FakeWriterRevisionToolkit:
        def apply_string_replace(self, **_kwargs) -> str:
            return json.dumps({
                'string_replace_result': {'replaced': 1},
                'revised_document': '# Revised title\n\nUpdated body.\n',
            })

    monkeypatch.setattr(tools, 'require_context', lambda: context)
    monkeypatch.setattr(tools, 'WriterRevisionToolkit', FakeWriterRevisionToolkit)
    base_document_path = tmp_path / 'draft.md'
    base_document_path.write_text('# Original\n', encoding='utf-8')
    writing_context_path = tmp_path / 'context.json'
    writing_context_path.write_text('{}', encoding='utf-8')
    revision_set_path = tmp_path / 'revisions.json'
    revision_set_path.write_text('{}', encoding='utf-8')

    result = tools.writer_apply_revision(
        str(base_document_path),
        str(writing_context_path),
        str(revision_set_path),
    )

    assert Path(result['revised_document']).read_text(encoding='utf-8') == (
        '# Revised title\n\nUpdated body.\n'
    )
    assert [event['type'] for event in events] == [
        'artifact_stream_start',
        'artifact_stream',
        'artifact_stream_end',
    ]
    assert all(event['slot'] == 'draft_document' for event in events)
    assert all(event['content_type'] == 'text/markdown' for event in events)
    assert events[1]['delta'] == '# Revised title\n\nUpdated body.\n'


def test_initial_feishu_ir_revision_publishes_from_apply_result(monkeypatch, tmp_path):
    tools = _load_tools_module()
    context = SimpleNamespace(
        workspace_path=str(tmp_path),
        params={
            'step_id': 'write_document',
            'initial_cloud_write_required': True,
        },
        emit=lambda _event: None,
    )
    publish_calls: list[dict] = []

    class FakeWriterRevisionToolkit:
        def apply_revision(self, **kwargs) -> str:
            assert kwargs['sync_provider'] is False
            return json.dumps({
                'patch_result': {'success': True},
                'revised_document': {'document_id': 'local-draft', 'stage': 'draft'},
            })

    def fake_publish_revision(**kwargs) -> dict:
        publish_calls.append(kwargs)
        return {
            'draft_document': '/provider-confirmed.lmd',
            'publish_result': '/provider-write-result.json',
            'published_link': 'https://example.feishu.cn/docx/doc-1',
        }

    monkeypatch.setattr(tools, 'require_context', lambda: context)
    monkeypatch.setattr(tools, 'WriterRevisionToolkit', FakeWriterRevisionToolkit)
    monkeypatch.setattr(tools, 'writer_publish_revision', fake_publish_revision)
    monkeypatch.setattr(tools, '_save_writer_document', lambda *_args, **_kwargs: '/local-draft.lmd')
    monkeypatch.setattr(tools, '_save_json_artifact', lambda name, *_args, **_kwargs: f'/{name}.json')
    base_document_path = tmp_path / 'source_document.lmd'
    base_document_path.write_text('{}', encoding='utf-8')
    writing_context_path = tmp_path / 'context.json'
    writing_context_path.write_text('{}', encoding='utf-8')
    revision_set_path = tmp_path / 'revisions.json'
    revision_set_path.write_text('{}', encoding='utf-8')

    result = tools.writer_apply_revision(
        str(base_document_path),
        str(writing_context_path),
        str(revision_set_path),
    )

    assert publish_calls == [{
        'source_document_path': str(base_document_path),
        'revision_set_path': str(revision_set_path),
        'media_assets_path': '',
    }]
    assert result['revised_document'] == '/provider-confirmed.lmd'
    assert result['write_result'] == '/provider-write-result.json'
    assert result['published_link'] == 'https://example.feishu.cn/docx/doc-1'


def test_later_feishu_ir_revision_stays_local(monkeypatch, tmp_path):
    tools = _load_tools_module()
    context = SimpleNamespace(
        workspace_path=str(tmp_path),
        params={'step_id': 'write_document'},
        emit=lambda _event: None,
    )

    class FakeWriterRevisionToolkit:
        def apply_revision(self, **kwargs) -> str:
            assert kwargs['sync_provider'] is False
            return json.dumps({
                'patch_result': {'success': True},
                'revised_document': {'document_id': 'local-draft', 'stage': 'draft'},
            })

    monkeypatch.setattr(tools, 'require_context', lambda: context)
    monkeypatch.setattr(tools, 'WriterRevisionToolkit', FakeWriterRevisionToolkit)
    monkeypatch.setattr(
        tools,
        'writer_publish_revision',
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError('later revision must stay local')),
    )
    monkeypatch.setattr(tools, '_save_writer_document', lambda *_args, **_kwargs: '/local-draft.lmd')
    monkeypatch.setattr(tools, '_save_json_artifact', lambda name, *_args, **_kwargs: f'/{name}.json')
    base_document_path = tmp_path / 'draft_document.lmd'
    base_document_path.write_text('{}', encoding='utf-8')
    writing_context_path = tmp_path / 'context.json'
    writing_context_path.write_text('{}', encoding='utf-8')
    revision_set_path = tmp_path / 'revisions.json'
    revision_set_path.write_text('{}', encoding='utf-8')

    result = tools.writer_apply_revision(
        str(base_document_path),
        str(writing_context_path),
        str(revision_set_path),
    )

    assert result['revised_document'] == '/local-draft.lmd'
    assert result['write_result'] == ''
