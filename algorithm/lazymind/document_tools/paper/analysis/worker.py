"""Trusted statistical program. JSON enters via stdin; JSON/SVG leave via stdout.

This file also runs standalone in a minimal Python environment or Docker image.
It never evaluates user/model code and never fetches data or installs packages.
"""
from __future__ import annotations

import io
import json
import math
import sys


def compute(payload: dict) -> dict:
    import numpy as np
    from scipy import stats

    table, plan = payload['table'], payload['plan']
    columns, rows = table['columns'], table['rows']
    gi = columns.index(plan['group_by']) if plan['group_by'] is not None else None
    summaries, comparisons, limitations = [], [], []
    missing = sum(cell is None for row in rows for cell in row)
    if missing:
        limitations.append(f'原表有 {missing} 个缺失单元格；逐指标排除 null，不填 0、不修改原表。')
    rng = np.random.default_rng(42)
    for metric in plan['metrics']:
        mi = columns.index(metric)
        by_group = {}
        for group in plan['groups']:
            selected = [r for r in rows if gi is None or r[gi] == group]
            values = np.array([r[mi] for r in selected if r[mi] is not None], dtype=float)
            if not len(values):
                raise ValueError(f'{group}/{metric} 没有非缺失观测')
            by_group[group] = values
            mean = float(np.mean(values))
            ci = None
            if len(values) >= 3:
                means = []
                for _ in range(50):
                    means.extend(rng.choice(values, size=(20, len(values)), replace=True).mean(axis=1).tolist())
                ci = np.quantile(means, [0.025, 0.975]).tolist()
            else:
                limitations.append(f'{group}/{metric} 只有 {len(values)} 个观测，不报告置信区间。')
            summaries.append({'group': group, 'metric': metric, 'count': len(values),
                              'missing': len(selected) - len(values), 'mean': mean,
                              'std': float(np.std(values, ddof=1)) if len(values) >= 2 else None,
                              'min': float(np.min(values)), 'max': float(np.max(values)),
                              'median': float(np.median(values)), 'ci95': ci})
        baseline = plan['baseline']
        if baseline is not None:
            for group in plan['groups']:
                if group == baseline:
                    continue
                a, b = by_group[group], by_group[baseline]
                comparison = {'group': group, 'baseline': baseline, 'metric': metric,
                              'mean_diff': float(np.mean(a) - np.mean(b)), 'test': plan['test'],
                              'p_value': None, 'p_adjusted': None, 'paired_count': None}
                if plan['test'] == 'paired_t':
                    pi = columns.index(plan['pair_by'])
                    pairs = {}
                    for name in (group, baseline):
                        pairs[name] = {}
                        for row in rows:
                            if row[gi] != name:
                                continue
                            key = row[pi]
                            if key is None or type(key) not in (str, int) or (isinstance(key, str) and not key.strip()):
                                raise ValueError('配对键必须是非空字符串或整数')
                            # Keep integer and string IDs distinct.
                            key = (type(key).__name__, key)
                            if key in pairs[name]:
                                raise ValueError(f'{name} 存在重复配对键，不能自动合并')
                            pairs[name][key] = row[mi]
                    common = [key for key in pairs[group] if key in pairs[baseline]
                              and pairs[group][key] is not None and pairs[baseline][key] is not None]
                    a = np.array([pairs[group][k] for k in common], dtype=float)
                    b = np.array([pairs[baseline][k] for k in common], dtype=float)
                    comparison['paired_count'] = len(common)
                    comparison['mean_diff'] = float(np.mean(a - b)) if len(common) else None
                    discarded = len(pairs[group]) + len(pairs[baseline]) - 2 * len(common)
                    if discarded:
                        limitations.append(f'{group} 对 {baseline}/{metric} 排除 {discarded} 条未匹配或缺失记录。')
                if plan['test'] != 'none':
                    if min(len(a), len(b)) < 3:
                        raise ValueError(f'{group} 对 {baseline}/{metric} 有效样本不足 3，不能完成所请求检验')
                    spread = (np.std(a - b, ddof=1) if plan['test'] == 'paired_t'
                              else max(np.std(a, ddof=1), np.std(b, ddof=1)))
                    if spread == 0:
                        raise ValueError('检验方差为零，不能返回有效 t 检验结论')
                    test = (stats.ttest_rel(a, b) if plan['test'] == 'paired_t'
                            else stats.ttest_ind(a, b, equal_var=False))
                    if not math.isfinite(float(test.statistic)) or not math.isfinite(float(test.pvalue)):
                        raise ValueError('统计检验产生非有限值，请检查数据与方法适用性')
                    comparison['p_value'] = float(test.pvalue)
                comparisons.append(comparison)
    tested = sorted((c for c in comparisons if c['p_value'] is not None), key=lambda c: c['p_value'])
    previous = 0.0
    for index, comparison in enumerate(tested):
        previous = max(previous, min(1.0, comparison['p_value'] * (len(tested) - index)))
        comparison['p_adjusted'] = previous
    if plan['test'] == 'none':
        limitations.append('本次为描述性分析，未进行显著性检验；均值差异不能直接解释为显著提升。')
    else:
        limitations.append('检验按指定的独立/配对设计执行；观测独立性、抽样设计和因果关系仍需研究者核验。')
    limitations.append('Bootstrap 区间使用观测可独立重采样的假设，样本数量本身不保证该假设成立。')
    return {'data_sha256': plan['data_sha256'], 'row_count': len(rows), 'statistics': summaries,
            'comparisons': comparisons, 'limitations': list(dict.fromkeys(limitations))}


def draw(result: dict, plan: dict) -> list[dict]:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    available = {f.name for f in font_manager.fontManager.ttflist}
    fonts = [f for f in ('Noto Sans CJK SC', 'Noto Sans CJK JP', 'Microsoft YaHei',
                         'PingFang SC', 'Arial Unicode MS', 'DejaVu Sans') if f in available]
    palette = ['#4477AA', '#EE6677', '#228833', '#AA3377', '#66CCEE', '#CCBB44', '#BBBBBB']
    charts = []
    with plt.rc_context({'svg.fonttype': 'none', 'svg.hashsalt': plan['data_sha256'],
                         'font.family': fonts, 'font.size': 10, 'axes.unicode_minus': False}):
        for index, metric in enumerate(plan['metrics']):
            fig, ax = plt.subplots(figsize=(max(6.5, len(plan['groups']) * 0.55), 4.2), layout='constrained')
            try:
                entries = [s for s in result['statistics'] if s['metric'] == metric]
                x = list(range(len(entries)))
                values = [s['mean'] for s in entries]
                ax.bar(x, values, color=[palette[i % len(palette)] for i in x], width=0.65)
                for i, entry in enumerate(entries):
                    if entry['ci95'] is not None:
                        low, high = entry['ci95']
                        ax.vlines(i, low, high, color='#222222', linewidth=1.1)
                        ax.hlines([low, high], i - 0.10, i + 0.10, color='#222222', linewidth=1.1)
                labels = [s['group'] + f"\n(n={s['count']})" for s in entries]
                ax.set_xticks(x, labels, rotation=30 if len(entries) > 4 else 0,
                              ha='right' if len(entries) > 4 else 'center')
                ax.set_title(metric)
                ax.set_ylabel('Mean')
                ax.axhline(0, color='#555555', linewidth=0.6)
                ax.set_axisbelow(True)
                ax.grid(axis='y', color='#dddddd', linewidth=0.6)
                ax.spines[['top', 'right']].set_visible(False)
                ax.margins(y=0.15)
                buf = io.StringIO()
                fig.savefig(buf, format='svg', metadata={'Date': None, 'Creator': 'LazyMind F4'})
                svg = buf.getvalue()
                # Matplotlib adds an external public DOCTYPE; publish only the SVG root.
                charts.append({'id': f'fig_metric_{index + 1}', 'title': metric,
                               'columns': [*([plan['group_by']] if plan['group_by'] else []), metric],
                               'svg': svg[svg.index('<svg'):]})
            finally:
                plt.close(fig)
    return charts


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        result = compute(payload)
        result['charts'] = [] if payload.get('statistics_only') else draw(result, payload['plan'])
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except ImportError as exc:
        print(json.dumps({'error': f'分析环境缺少依赖: {exc.name}；请安装 numpy/scipy/matplotlib'}, ensure_ascii=False))
        raise SystemExit(2)
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        raise SystemExit(2)


if __name__ == '__main__':
    main()
