"""Writer-plugin 工具函数。
每个工具读取上游 artifact 文件，把输出写到 SubAgent 工作区，并返回输出文件的绝对路径，与 get_artifact 的返回一致。
工具函数本身不负责落库，主 Agent 在 step 结束时调用 `save_artifact(content_type='file', value=<路径>)` 完成提交。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict

import lazyllm
from lazyllm import LOG, AutoModel

from lazymind.chat.engine.subagent.context import require_context
from lazyllm.tools.writer.data_models import (
    InputResource,
    SectionInstruction,
    TargetDocument,
    WritingTask,
)
from lazyllm.tools.writer.tools import (
    WriterContextTools,
    WriterDraftingTools,
    WriterPlanningTools,
    WriterQualityTools,
    WriterResourceTools,
)


def _workspace_root() -> Path:
    ctx = require_context()
    return Path(ctx.workspace_path) if ctx.workspace_path else Path('/tmp')


def _read_artifact_file(path: str) -> Any:
    """读取 plugin workspace 中的 artifact 文件，优先解包 Artifact 格式的 data 字段。"""
    LOG.info(f'[writer-tool] _read_artifact_file begin path={path} exists={os.path.exists(path)}')
    if not os.path.exists(path):
        LOG.error(f'[writer-tool] _read_artifact_file FILE NOT FOUND path={path}')
        raise FileNotFoundError(path)
    with open(path, 'r', encoding='utf-8') as fh:
        raw = json.load(fh)
    if isinstance(raw, dict) and 'data' in raw:
        raw = raw['data']
    LOG.info(f'[writer-tool] _read_artifact_file OK path={path} data={raw}')
    return raw


from lazyllm.tools.writer.utils import save_artifact_json


def _safe_doc_title(title: str) -> str:
    title = (title or '').strip()
    title = re.sub(r'[\\/:*?"<>|\r\n]+', ' ', title)
    title = re.sub(r'\s+', ' ', title).strip()
    return title or 'AI 写作成稿'


def _strip_markdown_suffix(path: str) -> str:
    return path[:-3] if path.lower().endswith('.md') else path


def _has_feishu_auth() -> bool:
    try:
        auth_map = lazyllm.globals.config['dynamic_fs_auth'] or {}
    except Exception:
        return False
    if not isinstance(auth_map, dict):
        return False
    token = auth_map.get('feishu') or auth_map.get('lark')
    if isinstance(token, str):
        return bool(token.strip())
    if isinstance(token, (list, tuple)):
        return any(isinstance(item, str) and item.strip() for item in token)
    return bool(token)


def parse_feishu_export_uri(target_hint: str = '', title: str = '') -> Dict[str, Any]:
    """Resolve an explicit Feishu write-back URI from user hint.

    Args:
        target_hint: User-facing hint. This resolver only accepts an explicit
            ``feishu@<space_id>:/path/title.md`` URI today.
        title: Preferred document title from the WritingOutput artifact.

    Returns:
        A dict with status and uri. When no explicit target is provided, this
        uses the Feishu Drive root, which is available to every authorized
        Feishu account.
    """
    hint = (target_hint or '').strip()
    preferred_title = _safe_doc_title(title)

    if not hint.startswith('feishu@'):
        return {'status': 'resolved', 'uri': f'feishu:/{preferred_title}.md', 'source': 'default_drive_root'}

    if ':/' not in hint:
        return {
            'status': 'invalid_target',
            'message': 'Feishu target URI must look like feishu@<space_id>:/path/title.md.',
            'target_hint': hint,
        }
    if hint.lower().endswith('.md'):
        uri = hint
    else:
        uri = f'{_strip_markdown_suffix(hint).rstrip("/")}/{preferred_title}.md'
    return {'status': 'resolved', 'uri': uri, 'source': 'explicit_uri'}


def _read_writing_output_content(writing_output_path: str) -> Dict[str, str]:
    data = _read_artifact_file(writing_output_path)
    if not isinstance(data, dict):
        raise TypeError('writing_output_path must point to a WritingOutput artifact.')
    content = str(data.get('content') or '').strip()
    if not content:
        raise ValueError('WritingOutput.content is empty; nothing to export.')
    return {
        'content': content,
        'title': str(data.get('title') or '').strip(),
    }


def build_writing_task(query: str) -> str:
    """构造 WritingTask 并产出 writing_task Artifact 文件。

    Args:
        query: 用户原始写作请求（来自 user_input）。

    Returns:
        writing_task Artifact 文件的绝对路径。
    """
    LOG.info(f'[writer-tool] build_writing_task input query={query!r}')
    task = WritingTask(query=query, task_type='write') # TODO: 借助LLM进行精细化的构造
    path = _workspace_root() / 'writing_task.json'
    save_artifact_json(task, str(path), created_by='build_writing_task')
    LOG.info(f'[writer-tool] build_writing_task produced writing_task artifact path={path}')
    return str(path)


def profile_resources(writing_task_path: str, user_input: str) -> str:
    """产出 resource_profiles Artifact 文件。

    Args:
        writing_task_path: 上一步产出的 writing_task Artifact 文件绝对路径。
        user_input: 用户原始提示词，用于从中抽取飞书链接等 InputResource。

    Returns:
        resource_profiles Artifact 文件的绝对路径。
    """
    LOG.info(f'[writer-tool] profile_resources input writing_task_path={writing_task_path} user_input={user_input!r}')
    _read_artifact_file(writing_task_path)
    ctx = require_context()
    files_by_turn = ctx.params.get('history_files_per_turn') or {}
    all_files = [p for paths in files_by_turn.values() for p in paths]
    LOG.info(f'[writer-tool] profile_resources history_files_per_turn={files_by_turn} all_files_count={len(all_files)} all_files={all_files}')

    feishu_pattern = re.compile(r'https?://[A-Za-z0-9.\-]+\.feishu\.cn/\S+')
    seen_urls: set[str] = set()
    feishu_urls: list[str] = []
    for match in feishu_pattern.finditer(user_input or ''):
        url = match.group(0)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        feishu_urls.append(url)
    LOG.info(f'[writer-tool] profile_resources feishu_urls={feishu_urls} count={len(feishu_urls)}')

    input_resources: list[InputResource] = []
    for abs_path in all_files:
        input_resources.append(InputResource(
            resource_id=os.path.basename(abs_path), resource_type='file', uri=abs_path,
            title=os.path.basename(abs_path), mime_type=None, summary=None, meta={},
        ))
    for idx, url in enumerate(feishu_urls):
        input_resources.append(InputResource(
            resource_id=f'feishu_{idx}', resource_type='url', uri=url,
            title=None, mime_type=None, summary=None, meta={'provider': 'feishu', 'role': 'background'},
        ))
    LOG.info(f'[writer-tool] profile_resources input_resources={[r.model_dump() for r in input_resources]}')
    result = WriterResourceTools(
        llm=AutoModel(model='llm'),
        artifact_store=str(_workspace_root()),
    ).profile_resources(task=writing_task_path, input_resources=input_resources)
    LOG.info(f'[writer-tool] profile_resources produced resource_profiles artifact counts={result["metadata"]["counts"]}')
    return result['artifact_path']


def create_writing_context(writing_task_path: str, resource_profiles_path: str) -> str:
    """产出 writing_context Artifact 文件。

    Args:
        writing_task_path: writing_task Artifact 文件绝对路径。
        resource_profiles_path: resource_profiles Artifact 文件绝对路径。

    Returns:
        writing_context Artifact 文件的绝对路径。
    """
    LOG.info(f'[writer-tool] create_writing_context input writing_task_path={writing_task_path} resource_profiles_path={resource_profiles_path}')
    _read_artifact_file(writing_task_path)
    _read_artifact_file(resource_profiles_path)
    result = WriterContextTools(
        llm=None,
        artifact_store=str(_workspace_root()),
    ).create_writing_context(task=writing_task_path, resource_profiles=resource_profiles_path)
    LOG.info(f'[writer-tool] create_writing_context produced writing_context artifact {result}')
    return result['artifact_path']


def generate_outline(writing_task_path: str, writing_context_path: str) -> str:
    """产出 outline Artifact 文件。

    Args:
        writing_task_path: writing_task Artifact 文件绝对路径。
        writing_context_path: writing_context Artifact 文件绝对路径。

    Returns:
        outline Artifact 文件的绝对路径。
    """
    LOG.info(f'[writer-tool] generate_outline input writing_task_path={writing_task_path} writing_context_path={writing_context_path}')
    _read_artifact_file(writing_task_path)
    _read_artifact_file(writing_context_path)
    result = WriterPlanningTools(
        llm=AutoModel(model='llm'),
        artifact_store=str(_workspace_root()),
    ).generate_outline(task=writing_task_path, context=writing_context_path)
    LOG.info(f'[writer-tool] generate_outline produced outline artifact {result}')
    return result['artifact_path']


def generate_section_instructions(
    outline_path: str,
    writing_context_path: str,
    review_report_path: str = '',
) -> str:
    """产出 section_instructions Artifact 文件，包含完整 SectionInstructionList。

    Args:
        outline_path: outline Artifact 文件绝对路径。
        writing_context_path: writing_context Artifact 文件绝对路径。
        review_report_path: review_report Artifact 文件绝对路径（可选）。

    Returns:
        section_instructions Artifact 文件的绝对路径。
    """
    LOG.info(
        '[writer-tool] generate_section_instructions input '
        f'outline_path={outline_path} '
        f'writing_context_path={writing_context_path} '
        f'review_report_path={review_report_path!r}'
    )
    _read_artifact_file(outline_path)
    _read_artifact_file(writing_context_path)
    execution_results: Any = None
    if review_report_path:
        execution_results = _read_artifact_file(review_report_path)
    result = WriterPlanningTools(
        llm=AutoModel(model='llm'),
        artifact_store=str(_workspace_root()),
    ).generate_section_instructions(
        outline=outline_path,
        context=writing_context_path,
        execution_results=execution_results,
    )
    LOG.info(f'[writer-tool] generate_section_instructions produced section_instructions artifact {result}')
    return result['artifact_path']


def generate_draft_section(
    writing_task_path: str,
    section_instructions_path: str,
    writing_context_path: str,
) -> str:
    """按已生成章节文件数量产出下一个 draft_section Artifact 文件。

    Args:
        writing_task_path: writing_task 文件路径。
        section_instructions_path: SectionInstructionList 文件路径。
        writing_context_path: writing_context 文件路径。

    Returns:
        draft_section 文件的绝对路径。全部章节生成完毕时返回空字符串。
    """
    LOG.info(
        '[writer-tool] generate_draft_section input '
        f'writing_task_path={writing_task_path} '
        f'section_instructions_path={section_instructions_path} '
        f'writing_context_path={writing_context_path}'
    )
    _read_artifact_file(writing_task_path)
    _read_artifact_file(writing_context_path)
    section_instructions = _read_artifact_file(section_instructions_path)
    if not isinstance(section_instructions, dict) or not isinstance(section_instructions.get('instructions'), list):
        raise TypeError('section_instructions_path must point to a SectionInstructionList artifact.')

    draft_sections_dir = _workspace_root() / 'draft_sections'
    draft_sections_dir.mkdir(parents=True, exist_ok=True)
    previous_paths = sorted(str(path) for path in draft_sections_dir.glob('draft_section_*.json'))
    next_index = len(previous_paths)
    instructions = section_instructions['instructions']
    if next_index >= len(instructions):
        LOG.info(
            '[writer-tool] generate_draft_section reached end '
            f'previous_count={len(previous_paths)} instruction_count={len(instructions)}'
        )
        return ''

    instruction = SectionInstruction.model_validate(instructions[next_index])
    previous_sections = [_read_artifact_file(path) for path in previous_paths]

    result = WriterDraftingTools(
        llm=AutoModel(model='llm'),
        artifact_store=str(draft_sections_dir),
    ).generate_draft_section(
        task=writing_task_path,
        section_instruction=instruction,
        context=writing_context_path,
        previous_sections=previous_sections,
    )
    LOG.info(f'[writer-tool] generate_draft_section produced draft_section artifact path={result["artifact_path"]} raw_result={result}')
    return result['artifact_path']


def assemble_draft_document(
    draft_sections_anchor_path: str,
    writing_context_path: str,
    outline_path: str = '',
) -> str:
    """合并多个 draft_section 产出 draft_document Artifact 文件。

    Args:
        draft_sections_anchor_path: 任一 draft_section 文件路径，或 draft_sections 目录路径。
        writing_context_path: writing_context 文件路径。
        outline_path: outline 文件路径。

    Returns:
        draft_document 文件的绝对路径。
    """
    LOG.info(
        '[writer-tool] assemble_draft_document input '
        f'draft_sections_anchor_path={draft_sections_anchor_path} '
        f'writing_context_path={writing_context_path} '
        f'outline_path={outline_path}'
    )
    anchor = Path(draft_sections_anchor_path)
    draft_sections_dir = anchor if anchor.is_dir() else anchor.parent
    draft_sections_paths = sorted(str(path) for path in draft_sections_dir.glob('draft_section_*.json'))
    if not draft_sections_paths:
        raise ValueError('draft_sections_anchor_path must point to a generated draft_sections directory or file.')
    for path in draft_sections_paths:
        _read_artifact_file(path)
    _read_artifact_file(writing_context_path)
    outline_ref = outline_path or None
    if outline_ref:
        _read_artifact_file(outline_ref)

    result = WriterDraftingTools(
        llm=None,
        artifact_store=str(_workspace_root()),
    ).generate_draft_document(
        draft_sections=draft_sections_paths,
        context=writing_context_path,
        outline=outline_ref,
    )
    LOG.info(f'[writer-tool] assemble_draft_document produced draft_document artifact {result}')
    return result['artifact_path']


def update_writing_context(content_artifact_path: str, writing_context_path: str) -> str:
    """基于内容 artifact 更新 writing_context Artifact 文件。

    Args:
        content_artifact_path: 用于更新上下文的内容 artifact 文件路径。
        writing_context_path: writing_context 文件路径。

    Returns:
        writing_context 文件的绝对路径。
    """
    LOG.info(f'[writer-tool] update_writing_context input content_artifact_path={content_artifact_path} writing_context_path={writing_context_path}')
    _read_artifact_file(content_artifact_path)
    _read_artifact_file(writing_context_path)
    result = WriterContextTools(
        llm=None,
        artifact_store=str(_workspace_root()),
    ).update_writing_context(artifacts=content_artifact_path, context=writing_context_path)
    LOG.info(f'[writer-tool] update_writing_context produced writing_context artifact {result}')
    return result['artifact_path']


def check_consistency(draft_path: str, writing_context_path: str) -> Dict[str, str]:
    """产出 review_report Artifact 文件并返回 validate_draft_document 的内容摘要。

    Args:
        draft_path: draft_document 文件路径。
        writing_context_path: writing_context 文件路径。

    Returns:
        两条字段，需要分别调用 `save_artifact(content_type='file', key='review_report')`
        与 `save_artifact(content_type='text', key='review_summary')` 进行落库。
    """
    LOG.info(f'[writer-tool] check_consistency input draft_path={draft_path} writing_context_path={writing_context_path}')
    _read_artifact_file(draft_path)
    _read_artifact_file(writing_context_path)
    result = WriterQualityTools(
        llm=AutoModel(model='llm'),
        artifact_store=str(_workspace_root()),
    ).validate_draft_document(
        draft_document=draft_path,
        context=writing_context_path,
    )
    returned: Dict[str, str] = {
        'review_report': result['artifact_path'],
        'review_summary': result['summary'],
    }
    LOG.info(f'[writer-tool] check_consistency produced {returned}')
    return returned


def generate_writing_output(
    draft_path: str, review_report_path: str, writing_context_path: str,
) -> Dict[str, str]:
    """产出两类 writing_output Artifact 文件。

    Args:
        draft_path: draft_document 文件路径。
        review_report_path: review_report 文件路径，用于确认审阅已完成。
        writing_context_path: writing_context 文件路径。

    Returns:
        两条绝对路径，需要分别调用 `save_artifact(content_type='file', key=<key>, value=<path>)` 进行落库。
    """
    LOG.info(
        '[writer-tool] generate_writing_output input '
        f'draft_path={draft_path} review_report_path={review_report_path} '
        f'writing_context_path={writing_context_path}'
    )
    _read_artifact_file(draft_path)
    _read_artifact_file(review_report_path)
    _read_artifact_file(writing_context_path)
    result = WriterDraftingTools(
        llm=None,
        artifact_store=str(_workspace_root()),
    ).generate_writing_output(
        draft=draft_path,
        context=writing_context_path,
    )
    returned: Dict[str, str] = {
        'writing_output': result['artifact_path'],
        'writing_output_md': result['output_file_path'],
    }
    LOG.info(
        f'[writer-tool] generate_writing_output produced result={result}'
    )
    return returned


def export_to_feishu(writing_output_path: str, target_hint: str = '') -> Dict[str, str]:
    """Write the final WritingOutput markdown content back to Feishu.

    Args:
        writing_output_path: Path to the WritingOutput artifact produced by
            generate_writing_output.
        target_hint: Optional user hint for the destination. Currently this must
            contain an explicit ``feishu@<space_id>:/path/title.md`` URI unless a
            higher-level LazyMind resolver has already converted user intent into one.

    Returns:
        A dict containing ``feishu_export_result``: a JSON artifact path. The
        result artifact status is one of exported, invalid_target,
        permission_required, or failed.
    """
    LOG.info(
        '[writer-tool] export_to_feishu input '
        f'writing_output_path={writing_output_path} target_hint={target_hint!r}'
    )
    output = _read_writing_output_content(writing_output_path)
    if not _has_feishu_auth():
        raise PermissionError(
            'FEISHU_ACCOUNT_REQUIRED: 当前用户尚未配置可用于写回的飞书账号。'
            '请先在前端完成飞书 OAuth 授权并启用聊天可用账号，然后重试写回步骤。'
        )

    resolved = parse_feishu_export_uri(target_hint=target_hint, title=output.get('title', ''))
    result_path = _workspace_root() / 'feishu_export_result.json'

    if resolved.get('status') != 'resolved':
        save_artifact_json(
            resolved,
            str(result_path),
            schema_name='lazymind.writer.FeishuExportResult',
            created_by='export_to_feishu',
        )
        return {'feishu_export_result': str(result_path)}

    uri = str(resolved['uri'])
    try:
        write_result = WriterResourceTools(
            llm=None,
            artifact_store=str(_workspace_root()),
        ).write_to_document(
            markdown=output['content'],
            target_document=TargetDocument(uri=uri, adapter='feishu', title=output.get('title') or None),
        )
        export_result = {
            'status': 'exported',
            'uri': uri,
            'write_result': write_result,
            'write_result_path': write_result.get('artifact_path'),
        }
    except Exception as exc:
        message = str(exc)
        lowered = message.lower()
        status = 'permission_required' if any(
            token in lowered
            for token in ('permission', 'forbidden', 'unauthorized', 'auth', 'token', 'dynamic_fs_auth', 'scope')
        ) else 'failed'
        export_result = {
            'status': status,
            'uri': uri,
            'message': message,
        }
        LOG.exception(f'[writer-tool] export_to_feishu failed status={status} uri={uri}')

    save_artifact_json(
        export_result,
        str(result_path),
        schema_name='lazymind.writer.FeishuExportResult',
        created_by='export_to_feishu',
    )
    LOG.info(f'[writer-tool] export_to_feishu produced result_path={result_path}')
    return {'feishu_export_result': str(result_path)}
