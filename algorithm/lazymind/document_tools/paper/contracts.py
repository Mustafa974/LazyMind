"""Business-only contracts. Storage, versions and workflow routing stay outside."""
import copy
import os

from .analysis.inputs import normalize_data
from .analysis.validation import validate_charts, validate_svg

from .review_schema import SavedReview, text_digest

FIELDS = {
    'research_topic': ('papers', 'question', 'evidence'),
    'literature_review': ('papers', 'question', 'evidence'),
    'experiment': ('question', 'evidence', 'data'),
    'data_analysis': ('data', 'experiment', 'data_context', 'study_design'),
    'write_document': ('papers', 'question', 'evidence', 'analysis', 'chart', 'outline', 'manuscript',
                       'review', 'review_data'),
    'peer_review': ('manuscript', 'evidence', 'analysis'),
    'reading_reflection': ('papers', 'evidence'),
}
OUTPUTS = {
    'research_topic': ('question',), 'literature_review': ('evidence', 'papers'),
    'experiment': ('experiment',), 'data_analysis': ('analysis', 'charts'),
    'write_document': ('outline', 'manuscript'), 'peer_review': ('review', 'review_data'),
    'reading_reflection': ('reflection',),
}
ALIASES = {'refine_topic': 'research_topic', 'revise_document': 'write_document'}
MODES = {'research_topic': 'create', 'refine_topic': 'refine',
         'write_document': 'draft', 'revise_document': 'revise'}
KINDS = set().union(*FIELDS.values(), *OUTPUTS.values())


class MissingMaterials(ValueError):
    """The caller should ask for business materials, never a pool file."""


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} 必须是非空文本')


def validate_value(kind, value):
    if kind == 'review_data':
        SavedReview.model_validate(value)
        return
    if kind == 'analysis' and isinstance(value, dict):
        from .analysis.schema import validate_analysis
        validate_analysis(value)
        return
    if kind in ('data_context', 'study_design', 'constraints'):
        from .analysis.schema import DataContext, StudyDesign, Constraints, parse
        parse({'data_context': DataContext, 'study_design': StudyDesign, 'constraints': Constraints}[kind], value)
        return
    if kind == 'papers':
        if not isinstance(value, list):
            raise ValueError('papers 必须是论文列表')
        ids = set()
        for paper in value:
            if not isinstance(paper, dict) or set(paper) - {'id', 'title', 'text', 'url'}:
                raise ValueError('论文字段必须是 id/title/text/url')
            for key in ('id', 'title', 'text'):
                text(paper.get(key), f'paper.{key}')
            if 'url' in paper and not isinstance(paper['url'], str):
                raise ValueError('paper.url 必须是字符串')
            if paper['id'] in ids:
                raise ValueError('论文 id 重复')
            ids.add(paper['id'])
    elif kind == 'data':
        if isinstance(value, dict) and ('column_types' in value or 'tables' in value):
            from .analysis.schema import DataTable, DataBundle, parse
            parse(DataBundle if 'tables' in value else DataTable, value)
        else:
            normalize_data(value)
    elif kind == 'charts':
        if value == []:
            return
        if isinstance(value, list) and value and 'finding_ids' in value[0]:
            from .analysis.schema import Chart, parse
            for chart in value:
                parse(Chart, chart)
                validate_svg(chart['svg'])
        else:
            validate_charts(value)
    elif kind == 'experiment':
        if isinstance(value, dict) and {'code', 'logs'} <= set(value):
            from .analysis.schema import Experiment, parse
            parse(Experiment, value)
            return
        if not isinstance(value, dict) or set(value) != {'executed', 'plan', 'results', 'report'}:
            raise ValueError('experiment 必须包含 executed/plan/results/report')
        if type(value['executed']) is not bool:
            raise ValueError('executed 必须是布尔值')
        text(value['plan'], 'plan')
        text(value['report'], 'report')
        if value['results'] is not None:
            validate_value('data', value['results'])
        if value['executed'] and value['results'] is None:
            raise ValueError('已执行实验必须提供结果')
    else:
        text(value, kind)
        if kind == 'chart':
            validate_svg(value)


def validate_input(step, value):
    if step == 'data_analysis' and os.environ.get('LAZYMIND_PAPER_ANALYSIS_MODE', 'generated') != 'legacy':
        from .analysis.schema import normalize_input, ContractError
        try:
            return normalize_input(value)
        except ContractError as exc:
            if exc.code == 'MISSING_MATERIAL':
                raise MissingMaterials(str(exc)) from exc
            raise
    module = ALIASES.get(step, step)
    fields = FIELDS[module]
    allowed = {'topic', 'request', 'mode', *fields}
    if module == 'data_analysis':
        allowed.add('constraints')
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError(f'{step} 输入含未知字段')
    text(value.get('topic'), 'topic')
    if not isinstance(value.get('request', ''), str):
        raise ValueError('request 必须是字符串')
    result = {'topic': value['topic'], 'request': value.get('request', '')}
    if module == 'data_analysis' and 'constraints' in value:
        from .analysis.schema import Constraints, parse
        result['constraints'] = parse(Constraints, value['constraints'])
    if step in MODES:
        if value.get('mode', MODES[step]) != MODES[step]:
            raise ValueError(f'{step} mode 必须是 {MODES[step]}')
        result['mode'] = MODES[step]
    elif 'mode' in value:
        raise ValueError(f'{step} 不接受 mode')
    for field in fields:
        item = value.get(field, [] if field == 'papers' else None)
        if item is not None:
            if field == 'analysis' and module != 'data_analysis':
                text(item, field)  # Preserve the existing downstream input contract.
            else:
                validate_value(field, item)
        result[field] = copy.deepcopy(item)
    required = {'refine_topic': ('evidence',), 'peer_review': ('manuscript',),
                'revise_document': ('manuscript', 'review'), 'reading_reflection': ('papers',)}.get(step, ())
    missing = [name for name in required if not result[name]]
    if step == 'data_analysis':
        actual_experiment = (result['experiment'] and result['experiment']['executed']
                             and result['experiment']['results'] is not None)
        if not result['data'] and not actual_experiment:
            missing.append('数据或已执行实验结果')
        if result['data'] is not None and actual_experiment:
            raise ValueError('同时提供数据和已执行实验结果，请明确选择一个数据来源')
    if step == 'write_document':
        writing_materials = ('papers', 'question', 'evidence', 'analysis', 'outline', 'manuscript')
        if not any(result[k] for k in writing_materials):
            missing.append('研究材料、大纲或稿件')
    if missing:
        raise MissingMaterials('缺少材料: ' + '、'.join(missing))
    if step == 'revise_document' and result['review_data'] is not None:
        data = result['review_data']
        if (data['manuscript_sha256'] != text_digest(result['manuscript'])
                or data['review_sha256'] != text_digest(result['review'])):
            raise ValueError('稿件或审稿意见已修改，结构化意见已过期，请重新审稿后再修订')
    return result


def validate_output(module, value, inputs=None):
    if not isinstance(value, dict) or set(value) != {'is_demo', *OUTPUTS[module]}:
        raise ValueError(f'{module} 算法输出字段不符合约定')
    if type(value['is_demo']) is not bool:
        raise ValueError('输出 is_demo 必须是布尔值')
    if module == 'data_analysis' and isinstance(value['analysis'], dict):
        from .analysis.schema import AnalysisOutput, parse, validate_analysis
        from .analysis.schema import validate_charts as validate_structured_charts
        parse(AnalysisOutput, value, 'output')
        validate_analysis(value['analysis'], inputs)
        validate_structured_charts(value['charts'], value['analysis'])
        return value
    for key in OUTPUTS[module]:
        validate_value(key, value[key])
    if module == 'peer_review' and value['review_data']['review_sha256'] != text_digest(value['review']):
        raise ValueError('结构化审稿意见与 Markdown 报告不匹配')
    return value
