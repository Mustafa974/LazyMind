"""Plan first, then independently generate and render figures with bounded concurrency."""
from __future__ import annotations

from concurrent.futures import as_completed
import hashlib
import os

from .code import validate_code
from .data import bind_figure
from .executor import PlotEnvironmentError, configuration, execute_plot
from .model import PlotModelError, generate_code, plan_figures
from .skills import PlotSkills
from .style_profiles import bind_series
from .templates import compose_native_program, validate_native_program
from ..analysis.validation import validate_charts
from ..execution.threads import ThreadPoolExecutor


def prepare(table: dict, topic: str, request: str):
    configuration()  # Fail deployment mistakes before spending model calls.
    limits()
    skills = PlotSkills()
    statistics_plan, figure_plan = plan_figures(table, topic, request, skills)
    return statistics_plan, figure_plan, skills


def limits() -> tuple[int, int]:
    try:
        attempts = int(os.environ.get('LAZYMIND_PAPER_PLOT_ATTEMPTS', '3'))
        if not 1 <= attempts <= 5:
            raise ValueError
    except ValueError as exc:
        raise ValueError('绘图尝试次数必须是 1..5') from exc
    try:
        concurrency = int(os.environ.get('LAZYMIND_PAPER_PLOT_CONCURRENCY', '4'))
        if not 1 <= concurrency <= 4:
            raise ValueError
    except ValueError as exc:
        raise ValueError('绘图并发数必须是 1..4') from exc
    return attempts, concurrency


def draw_one(bound: dict, measured: dict, spec: dict, reference: str, attempts: int,
             code_generator=None) -> tuple[dict, dict]:
    previous, error = '', ''
    history = []
    for attempt in range(1, attempts + 1):
        try:
            previous = (code_generator or generate_code)(bound, measured, spec, reference, previous, error)
            previous = compose_native_program(previous, reference, spec)
            validate_code(previous)
            validate_native_program(previous, spec)
            rendered = execute_plot({'table': bound, 'statistics': measured, 'spec': spec,
                                     'data_sha256': measured['data_sha256'], 'code': previous})
            if set(rendered) != {'svg', 'exports'}:
                raise ValueError('绘图执行结果必须包含 svg/exports')
            chart = {'id': spec['id'], 'title': spec['title'], 'columns': spec['columns'], **rendered}
            if spec.get('caption'):
                chart['caption'] = spec['caption']
            validate_charts([chart])
            history.append({'attempt': attempt, 'status': 'passed_execution_and_file_checks'})
            trace = {'id': spec['id'], 'code': previous, 'data_contract': spec['data_contract'],
                     'caption': spec.get('caption', ''),
                     'style_contract': spec.get('style_contract', {}),
                     'code_sha256': hashlib.sha256(previous.encode()).hexdigest(), 'attempts': history,
                     'first_attempt_passed_execution_and_file_checks': attempt == 1}
            return chart, trace
        except (PlotEnvironmentError, PlotModelError):
            raise
        except (ValueError, TypeError, KeyError, SyntaxError, RuntimeError) as exc:
            current_error = str(exc)[-6000:]
            history.append({'attempt': attempt, 'status': 'failed', 'error': current_error,
                            'code_sha256': hashlib.sha256(previous.encode()).hexdigest()})
            error = current_error if len(history) == 1 else '\n'.join(
                f'Attempt {item["attempt"]}: {item["error"]}' for item in history)[-12000:]
    raise RuntimeError(f'图表 {spec["id"]} 在 {attempts} 次尝试后失败，未发布本轮图表: {error}')


def draw(table: dict, statistics: dict, plan: dict, skills: PlotSkills) -> tuple[list, dict]:
    attempts, concurrency = limits()
    # Resolve all resources and data before starting model calls. Each job owns its input and retry state.
    jobs = []
    for spec in plan['figures']:
        reference = skills.reference(spec['style'], spec.get('base_style'))
        styled = {**spec, 'style_contract': skills.style_contract(spec['style'], spec.get('base_style'))}
        bound, measured, styled = bind_figure(table, statistics, plan['analysis'], styled)
        styled['style_contract'] = bind_series(styled['style_contract'], styled['data_contract']['group_order'])
        jobs.append((bound, measured, styled, reference))
    workers = min(concurrency, len(jobs))
    ordered = [None] * len(jobs)
    # LazyLLM's executor propagates the request's session and ContextVars to keep the configured model role.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(draw_one, *job, attempts): index for index, job in enumerate(jobs)}
        try:
            for future in as_completed(futures):
                ordered[futures[future]] = future.result()
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    charts, traces = map(list, zip(*ordered))
    return charts, {'figure_plan': plan, 'skill_resources_sha256': dict(skills.hashes),
                    'concurrency': workers, 'visual_review': 'deferred_not_implemented', 'figures': traces}
