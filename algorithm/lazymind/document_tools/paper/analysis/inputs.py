"""Normalize structured measurements without reading files or inventing values."""
from __future__ import annotations

import copy
import hashlib
import json
import math

MAX_ROWS = 100_000
MAX_CELLS = 1_000_000
MAX_METRICS = 20
MAX_GROUPS = 20
OPTION_KEYS = {'group_by', 'metrics', 'baseline', 'pair_by', 'test'}


def normalize_data(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError('数据必须是结构化 JSON 对象')
    if 'runs' in value:
        if set(value) - {'runs', 'options', 'metadata'}:
            raise ValueError('运行指标只接受 runs/options/metadata')
        runs = value['runs']
        if not isinstance(runs, list) or not runs or len(runs) > MAX_ROWS:
            raise ValueError('runs 必须包含 1..100000 条实际运行记录')
        metrics, paired = [], any(isinstance(r, dict) and 'seed' in r for r in runs)
        metric_names = set()
        for run in runs:
            if not isinstance(run, dict) or set(run) - {'condition', 'seed', 'metrics'}:
                raise ValueError('每次运行只接受 condition/seed/metrics')
            if not isinstance(run.get('condition'), str) or not run['condition'].strip():
                raise ValueError('run.condition 必须是非空字符串')
            if paired and (type(run.get('seed')) not in (str, int) or run['seed'] == ''):
                raise ValueError('运行记录的 seed 必须全部提供，类型为字符串或整数')
            if not isinstance(run.get('metrics'), dict) or not run['metrics']:
                raise ValueError('run.metrics 必须是非空指标对象')
            for name, number in run['metrics'].items():
                if not isinstance(name, str) or not name.strip() or name in {'condition', 'seed'}:
                    raise ValueError('指标名不能为空或与 condition/seed 冲突')
                if number is not None and (type(number) not in (int, float) or not math.isfinite(number)):
                    raise ValueError(f'指标 {name} 必须是有限数值或 null')
                if name not in metric_names:
                    metric_names.add(name)
                    metrics.append(name)
                    if len(metrics) + 1 + int(paired) > 200:
                        raise ValueError('运行指标不能展开为超过 200 列的表格')
                    if len(runs) * (len(metrics) + 1 + int(paired)) > MAX_CELLS:
                        raise ValueError('运行指标不能展开为超过 100 万单元格的表格')
        columns = ['condition', *(['seed'] if paired else []), *metrics]
        rows = [[r['condition'], *([r['seed']] if paired else []),
                 *[r['metrics'].get(m) for m in metrics]] for r in runs]
        value = {'columns': columns, 'rows': rows, 'options': value.get('options', {}),
                 'metadata': value.get('metadata', {})}
    if set(value) - {'columns', 'rows', 'options', 'metadata'} or not {'columns', 'rows'} <= set(value):
        raise ValueError('表格必须包含 columns/rows，可选 options/metadata')
    columns, rows = value['columns'], value['rows']
    if (not isinstance(columns, list) or not 1 <= len(columns) <= 200
            or any(not isinstance(c, str) or not c.strip() for c in columns)
            or len(set(columns)) != len(columns)):
        raise ValueError('columns 必须包含 1..200 个非空且唯一的列名')
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
        raise ValueError('rows 必须包含 1..100000 行')
    if len(rows) * len(columns) > MAX_CELLS:
        raise ValueError('数据不能超过 100 万单元格')
    for row in rows:
        if not isinstance(row, list) or len(row) != len(columns):
            raise ValueError('数据行长度必须与列数相同')
        for cell in row:
            if cell is not None and type(cell) not in (str, int, float, bool):
                raise ValueError('单元格必须是 JSON 标量')
            if type(cell) in (int, float) and not math.isfinite(cell):
                raise ValueError('数据不允许 NaN/Infinity')
    options = value.get('options', {})
    if not isinstance(options, dict) or set(options) - OPTION_KEYS:
        raise ValueError('options 只接受 group_by/metrics/baseline/pair_by/test')
    for key in ('group_by', 'pair_by'):
        if key in options and options[key] is not None and options[key] not in columns:
            raise ValueError(f'options.{key} 必须引用实际存在的列')
    if 'metrics' in options and (
            not isinstance(options['metrics'], list) or not options['metrics']
            or any(not isinstance(m, str) or m not in columns for m in options['metrics'])
            or len(set(options['metrics'])) != len(options['metrics'])):
        raise ValueError('options.metrics 必须是不重复的有效列名列表')
    if options.get('test', 'none') not in ('none', 'welch_t', 'paired_t'):
        raise ValueError('options.test 仅支持 none/welch_t/paired_t')
    if 'baseline' in options and not isinstance(options['baseline'], str):
        raise ValueError('options.baseline 必须是实际分组标签字符串')
    metadata = value.get('metadata', {})
    if not isinstance(metadata, dict) or set(metadata) - {'description', 'columns'}:
        raise ValueError('metadata 只接受 description/columns')
    if 'description' in metadata and (not isinstance(metadata['description'], str)
                                      or len(metadata['description']) > 4000):
        raise ValueError('数据描述必须是 4000 字以内的文本')
    fields = metadata.get('columns', {})
    if not isinstance(fields, dict) or any(column not in columns for column in fields):
        raise ValueError('metadata.columns 必须引用实际列')
    for field in fields.values():
        if (not isinstance(field, dict) or set(field) - {'meaning', 'unit', 'role'}
                or any(not isinstance(text, str) or len(text) > 1000 for text in field.values())):
            raise ValueError('字段元数据只接受 meaning/unit/role 文本')
    result = {'columns': columns, 'rows': rows, 'options': options}
    if metadata:
        result['metadata'] = metadata
    return copy.deepcopy(result)


def data_digest(table: dict) -> str:
    data = {key: table[key] for key in ('columns', 'rows', 'metadata') if key in table}
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def make_plan(table: dict, request: str = '') -> dict:
    columns, rows, options = table['columns'], table['rows'], table['options']
    group = options.get('group_by')
    if 'group_by' not in options:
        preferred = {'method', 'condition', 'group', 'model', 'variant'}
        candidates = [c for i, c in enumerate(columns)
                      if any(row[i] is not None for row in rows)
                      and all(row[i] is None or isinstance(row[i], str) for row in rows)]
        named = [c for c in candidates if c.lower() in preferred]
        if len(named) == 1:
            group = named[0]
        elif len(candidates) == 1:
            group = candidates[0]
        elif candidates:
            raise ValueError('存在多列分类数据，请用 options.group_by 明确分组列')
    if group is not None:
        group_index = columns.index(group)
        if any(not isinstance(r[group_index], str) or not r[group_index].strip() for r in rows):
            raise ValueError('分组列必须是非空字符串，缺失分组需要先明确处理方式')
        groups = list(dict.fromkeys(r[group_index] for r in rows))
        if len(groups) > MAX_GROUPS:
            raise ValueError('分组超过 20 个，请先明确筛选范围')
    else:
        groups = ['Overall']
    excluded = {group, options.get('pair_by')}
    reserved = {'seed', 'id', 'run', 'run_id', 'replicate', 'fold'}
    metrics = options.get('metrics')
    if metrics is None:
        metrics = [c for i, c in enumerate(columns) if c not in excluded and c.lower() not in reserved
                   and any(r[i] is not None for r in rows)
                   and all(r[i] is None or type(r[i]) in (int, float) for r in rows)]
    if not metrics or len(metrics) > MAX_METRICS:
        raise ValueError('需要 1..20 个数值指标；请用 options.metrics 明确选择')
    for metric in metrics:
        i = columns.index(metric)
        if metric in excluded or not any(r[i] is not None for r in rows) or any(
                r[i] is not None and type(r[i]) not in (int, float) for r in rows):
            raise ValueError(f'指标 {metric} 必须为数值列，不能用分组列或配对键计算')
    test = options.get('test', 'none')
    baseline = options.get('baseline')
    if baseline is None:
        baseline = next((g for g in groups if g.lower() == 'baseline'), None)
    if baseline is not None and baseline not in groups:
        raise ValueError('baseline 不存在于实际分组中')
    if test != 'none' and (len(groups) < 2 or baseline is None):
        raise ValueError('统计检验需要至少两组，并通过 options.baseline 明确基线')
    if test == 'paired_t' and (options.get('pair_by') is None or options['pair_by'] == group):
        raise ValueError('配对检验必须用 options.pair_by 指定有效配对列')
    if test == 'none' and any(word in request.lower() for word in ('显著性检验', 't检验', 't 检验', 't-test')):
        raise ValueError('请在 options.test 指定检验，并明确 baseline 与配对方式')
    return {'group_by': group, 'groups': groups, 'metrics': metrics, 'baseline': baseline,
            'test': test, 'pair_by': options.get('pair_by'), 'data_sha256': data_digest(table)}
