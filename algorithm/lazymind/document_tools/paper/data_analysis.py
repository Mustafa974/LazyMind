"""Verified statistics plus skill-guided, model-generated scientific figures."""
import os

from .analysis.executor import execute
from .analysis.inputs import make_plan, normalize_data
from .analysis.report import render_report
from .analysis.validation import validate_result
from .plotting import pipeline as _plot_pipeline


def run(inputs: dict) -> dict:
    """Receive structured measurements, execute actual calculations, return SVGs."""
    analysis_mode = os.environ.get('LAZYMIND_PAPER_ANALYSIS_MODE', 'generated')
    if analysis_mode == 'generated':
        from .analysis.pipeline import run as generated_analysis
        return generated_analysis(inputs)
    if analysis_mode != 'legacy':
        raise ValueError('LAZYMIND_PAPER_ANALYSIS_MODE 仅支持 generated/legacy，不会自动降级')
    data, experiment = inputs.get('data'), inputs.get('experiment')
    executed = isinstance(experiment, dict) and experiment.get('executed') is True
    if data is not None and executed and experiment.get('results') is not None:
        raise ValueError('同时提供数据和已执行实验结果，请明确选择一个数据来源')
    if data is not None:
        source = '输入数据'
    elif executed and experiment.get('results') is not None:
        data, source = experiment['results'], '已执行实验结果'
    else:
        raise ValueError('缺少实际数据，未执行的实验方案不能作为测量结果')
    table = normalize_data(data)
    mode = os.environ.get('LAZYMIND_PAPER_PLOTTING', 'llm')
    provenance = None
    if mode == 'llm':
        plan, figures, skills = _plot_pipeline.prepare(table, inputs['topic'], inputs.get('request', ''))
        result = execute(table, plan, statistics_only=True)
        validate_result(result, table, plan, validate_figures=False)
        result['charts'], provenance = _plot_pipeline.draw(table, result, figures, skills)
    elif mode == 'basic':
        plan = make_plan(table, inputs.get('request', ''))
        result = execute(table, plan)
        validate_result(result, table, plan)
    else:
        raise ValueError('LAZYMIND_PAPER_PLOTTING 仅支持 llm/basic，不会自动降级')
    report = render_report(inputs['topic'], inputs.get('request', ''), result, plan, source,
                           plotting=provenance)
    return {'analysis': report, 'charts': result['charts'], 'is_demo': False}
