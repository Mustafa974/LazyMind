"""Check computed values and inert SVG before any result is published."""
from __future__ import annotations

import base64
import math
import re
import statistics
import xml.etree.ElementTree as ET


def validate_svg(svg: str) -> None:
    if not isinstance(svg, str) or not svg.strip() or len(svg.encode()) > 2 * 1024 * 1024:
        raise ValueError('图表必须是非空且不超过 2 MiB 的 SVG')
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)', svg, re.I):
        raise ValueError('SVG 不允许 DOCTYPE/实体声明')
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise ValueError('图表必须是有效 SVG') from exc
    if root.tag not in ('svg', '{http://www.w3.org/2000/svg}svg'):
        raise ValueError('图表根节点必须是 SVG')
    forbidden = {'script', 'foreignobject', 'image', 'a', 'iframe', 'object', 'embed',
                 'animate', 'animatemotion', 'animatetransform', 'set', 'audio', 'video'}
    for node in root.iter():
        if node.tag.rsplit('}', 1)[-1].lower() in forbidden:
            raise ValueError('SVG 包含主动内容或外部资源元素')
        for attribute, value in node.attrib.items():
            attribute = attribute.rsplit('}', 1)[-1].lower()
            if attribute.startswith('on') or attribute in {'src', 'base'}:
                raise ValueError('SVG 不允许事件处理器或外部资源')
            if attribute == 'href' and not re.fullmatch(r'#[A-Za-z_][\w:.-]*', value):
                raise ValueError('SVG 只允许图内资源引用')
        styles = [*node.attrib.values()]
        if node.tag.rsplit('}', 1)[-1].lower() == 'style':
            styles.append(node.text or '')
        for style in styles:
            if '\\' in style or '/*' in style:
                raise ValueError('SVG 不允许转义或混淆的资源样式')
            if re.search(r'@import|expression\s*\(|javascript\s*:', style, re.I):
                raise ValueError('SVG 不允许可执行样式或外部样式')
            for reference in re.findall(r'url\s*\((.*?)\)', style, re.I | re.S):
                if not re.fullmatch(r'#[A-Za-z_][\w:.-]*', reference.strip()):
                    raise ValueError('SVG 不允许外部 URL')


def validate_charts(charts) -> None:
    if not isinstance(charts, list) or not 1 <= len(charts) <= 20:
        raise ValueError('charts 必须包含 1..20 张图表')
    identifiers = set()
    for chart in charts:
        if (not isinstance(chart, dict) or not {'id', 'title', 'columns', 'svg'} <= set(chart)
                or set(chart) - {'id', 'title', 'columns', 'svg', 'exports', 'caption'}):
            raise ValueError('每张图表必须包含 id/title/columns/svg')
        identifier = chart['id']
        if (not isinstance(identifier, str)
                or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', identifier)
                or identifier in identifiers):
            raise ValueError('图表 ID 必须唯一且只包含字母、数字、下划线或短横线')
        identifiers.add(identifier)
        if not isinstance(chart['title'], str) or not chart['title'].strip():
            raise ValueError('图表标题不能为空')
        if 'caption' in chart and (not isinstance(chart['caption'], str)
                                   or not chart['caption'].strip() or len(chart['caption']) > 7100):
            raise ValueError('图注（含输入数据说明）必须是 1..7100 字的文本')
        columns = chart['columns']
        if (not isinstance(columns, list) or not columns
                or any(not isinstance(c, str) or not c.strip() for c in columns)
                or len(set(columns)) != len(columns)):
            raise ValueError('图表必须列出不重复的数据来源列')
        validate_svg(chart['svg'])
        if 'exports' in chart:
            exports = chart['exports']
            if not isinstance(exports, dict) or set(exports) != {'png', 'pdf'}:
                raise ValueError('图表导出必须同时包含 png/pdf')
            for kind, encoded in exports.items():
                if not isinstance(encoded, str) or len(encoded) > 6 * 1024 * 1024:
                    raise ValueError('图表导出内容过大或无效')
                try:
                    raw = base64.b64decode(encoded, validate=True)
                except ValueError as exc:
                    raise ValueError('图表导出不是有效 base64') from exc
                signature = b'\x89PNG\r\n\x1a\n' if kind == 'png' else b'%PDF-'
                if not raw.startswith(signature) or len(raw) > 4 * 1024 * 1024:
                    raise ValueError('图表导出格式错误或超过 4 MiB')


def validate_result(result: dict, table: dict, plan: dict, *, validate_figures: bool = True) -> None:
    expected_keys = {'data_sha256', 'row_count', 'statistics', 'comparisons', 'limitations', 'charts'}
    if not isinstance(result, dict) or set(result) != expected_keys:
        raise ValueError('分析执行结果字段不完整')
    if (result['data_sha256'] != plan['data_sha256'] or type(result['row_count']) is not int
            or result['row_count'] != len(table['rows'])):
        raise ValueError('分析执行结果与本次输入数据版本不一致')
    items = result['statistics']
    if not isinstance(items, list) or len(items) != len(plan['groups']) * len(plan['metrics']):
        raise ValueError('分析遗漏或增加了指标分组')
    actual = {}
    for item in items:
        key = (item.get('group'), item.get('metric'))
        if key in actual:
            raise ValueError('分析分组重复')
        actual[key] = item
    gi = table['columns'].index(plan['group_by']) if plan['group_by'] is not None else None
    for group in plan['groups']:
        selected = [r for r in table['rows'] if gi is None or r[gi] == group]
        for metric in plan['metrics']:
            item = actual.get((group, metric), {})
            mi = table['columns'].index(metric)
            values = [r[mi] for r in selected if r[mi] is not None]
            if (not values or type(item.get('count')) is not int or item['count'] != len(values)
                    or item.get('missing') != len(selected) - len(values)):
                raise ValueError('分析样本数与原始数据不一致')
            expected = {'mean': statistics.fmean(values), 'median': statistics.median(values),
                        'min': min(values), 'max': max(values),
                        'std': statistics.stdev(values) if len(values) >= 2 else None}
            for name, number in expected.items():
                observed = item.get(name)
                if number is None:
                    if observed is not None:
                        raise ValueError('单样本不能报告样本标准差')
                elif (type(observed) not in (int, float) or not math.isfinite(observed)
                      or not math.isclose(observed, number, rel_tol=1e-9, abs_tol=1e-12)):
                    raise ValueError(f'{group}/{metric} 的 {name} 与复算结果不一致')
            ci = item.get('ci95')
            if len(values) < 3:
                if ci is not None:
                    raise ValueError('样本不足时不能报告区间')
            elif (not isinstance(ci, list) or len(ci) != 2
                  or any(type(v) not in (int, float) or not math.isfinite(v) for v in ci)
                  or not min(values) <= ci[0] <= ci[1] <= max(values)):
                raise ValueError('置信区间无效')
    if not isinstance(result['limitations'], list) or any(not isinstance(s, str) for s in result['limitations']):
        raise ValueError('分析局限必须是字符串列表')
    if not isinstance(result['comparisons'], list):
        raise ValueError('对比结果必须是列表')
    for comparison in result['comparisons']:
        for name in ('mean_diff', 'p_value', 'p_adjusted'):
            value = comparison.get(name)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                raise ValueError('对比结果包含非有限数值')
            if name != 'mean_diff' and value is not None and not 0 <= value <= 1:
                raise ValueError('p 值超出有效范围')
    if not validate_figures:
        if result['charts'] != []:
            raise ValueError('纯统计计算不得混入图表')
        return
    validate_charts(result['charts'])
    if len(result['charts']) != len(plan['metrics']):
        raise ValueError('图表数量与指标计划不一致')
    for chart, metric in zip(result['charts'], plan['metrics']):
        expected_columns = [*([plan['group_by']] if plan['group_by'] is not None else []), metric]
        if chart['columns'] != expected_columns or chart['title'] != metric:
            raise ValueError('图表数据来源与本次分析计划不一致')
