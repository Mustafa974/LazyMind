"""Build evidence-bound prose from computed numbers, never model estimates."""
from __future__ import annotations

import json


def _text(value) -> str:
    return str(value).replace('|', '\\|').replace('\n', ' ').replace('<', '&lt;').replace('>', '&gt;')


def _number(value) -> str:
    return '—' if value is None else f'{value:.6g}'


def render_report(topic: str, request: str, result: dict, plan: dict, source: str,
                  *, plotting: dict | None = None) -> str:
    lines = [f'# 数据分析 · {_text(topic)}', '', '## 数据与方法', '',
             f'- 来源：{source}；原始数据 {result["row_count"]} 行。',
             f'- 数据 SHA256：`{result["data_sha256"]}`。',
             '- 对各数值指标计算有效样本数、缺失数、均值、样本标准差、中位数、最小值和最大值。',
             '- 样本数至少为 3 时，以固定随机种子 42、1000 次 Bootstrap 计算均值的 95% 区间。']
    if plotting:
        lines.append('- 图表由模型按数据及绘图风格生成代码，已完成执行与文件检查；本轮未进行看图审查。')
    else:
        lines.append('- 使用基础绘图模式：柱高为均值，误差线为可计算的 Bootstrap 区间。')
    if request:
        lines.append(f'- 本轮要求：{_text(request)}')
    lines.extend(['', '## 计算结果', '',
                  '| 分组 | 指标 | 有效 n | 缺失 | 均值 | 标准差 | 中位数 | 最小值 | 最大值 | 95% 区间 |',
                  '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |'])
    for item in result['statistics']:
        ci = item['ci95']
        interval = f'[{_number(ci[0])}, {_number(ci[1])}]' if ci is not None else '样本不足'
        values = [_text(item['group']), _text(item['metric']), str(item['count']), str(item['missing']),
                  *[_number(item[k]) for k in ('mean', 'std', 'median', 'min', 'max')], interval]
        lines.append('| ' + ' | '.join(values) + ' |')
    lines.extend(['', '## 结论', ''])
    if result['comparisons']:
        for comparison in result['comparisons']:
            difference = '有效配对样本的均值差' if comparison['paired_count'] is not None else '均值差'
            lines.append(f'- {_text(comparison["group"])} 相对 {_text(comparison["baseline"])} 的 '
                         f'{_text(comparison["metric"])} {difference}为 {_number(comparison["mean_diff"])}。')
            if comparison['p_value'] is not None:
                count = f'，有效配对 n={comparison["paired_count"]}' if comparison['paired_count'] is not None else ''
                lines.append(f'  指定检验：{comparison["test"]}{count}；p={_number(comparison["p_value"])}，'
                             f'Holm 校正 p={_number(comparison["p_adjusted"])}。')
    else:
        lines.append('- 本次报告各组描述统计；没有指定有效基线，不自动选择某组作为优劣参照。')
    lines.extend(['', '## 局限', '', *['- ' + _text(note) for note in result['limitations']],
                  '', '## 图表', ''])
    for chart in result['charts']:
        lines.append(f'- `{chart["id"]}`：{_text(chart["title"])}；来源列：'
                     + '、'.join(_text(c) for c in chart['columns']) + '。')
        if chart.get('caption'):
            lines.append(f'  图注：{_text(chart["caption"])}')
    if plotting:
        for figure in plotting['figure_plan']['figures']:
            lines.append(f'- `{figure["id"]}` 绘图说明：{_text(figure["instructions"])}；'
                         f'风格：`{_text(figure["style"])}`。')
        lines.append('- 数值复核覆盖报告中的基础统计；尚未独立验证任意生成脚本的每个图元与数据是否一致。')
    exported = {k: v for k, v in result.items() if k != 'charts'}
    exported['analysis_plan'] = plan
    if plotting:
        exported['plotting'] = plotting
    lines.extend(['', '<details>', '<summary>结构化计算结果与复算参数</summary>', '', '```json',
                  json.dumps(exported, ensure_ascii=False, indent=2, allow_nan=False).replace('<', '\\u003c'),
                  '```', '', '</details>'])
    return '\n'.join(lines)
