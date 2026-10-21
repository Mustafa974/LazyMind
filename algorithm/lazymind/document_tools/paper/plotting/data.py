"""Bind each figure's complete input and derive its data contract before generation."""
from __future__ import annotations

import copy


def bind_figure(table: dict, statistics: dict, analysis: dict, spec: dict) -> tuple[dict, dict, dict]:
    # A model may mention a facet/filter in instructions while omitting its column.
    # Keep the complete input schema available from the FIRST generation through execution.
    # Requested columns remain first; the provenance distinguishes them from the full bound input.
    columns = list(dict.fromkeys([*spec['columns'], *table['columns']]))
    indices = [table['columns'].index(column) for column in columns]
    metadata = table.get('metadata', {})
    fields = {key: value for key, value in metadata.get('columns', {}).items() if key in columns}
    bound = {'columns': list(columns), 'rows': [[row[i] for i in indices] for row in table['rows']],
             'metadata': {**metadata, 'columns': fields}}
    measured = {key: copy.deepcopy(value) for key, value in statistics.items() if key != 'charts'}
    for key in ('statistics', 'comparisons'):
        measured[key] = [row for row in measured[key] if row['metric'] in columns]
    group_by = analysis['group_by'] if analysis['group_by'] in columns else None
    gi = columns.index(group_by) if group_by else None
    group_order = list(dict.fromkeys(row[gi] for row in bound['rows'])) if gi is not None else ['Overall']
    groups = []
    for group in group_order:
        rows = [row for row in bound['rows'] if gi is None or row[gi] == group]
        groups.append({'name': group, 'row_count': len(rows),
                       'non_null_counts': {column: sum(row[i] is not None for row in rows)
                                           for i, column in enumerate(columns)}})
    # Keep each metric's summaries aligned together; positions and values cannot come from different dimensions.
    summaries = {}
    for row in measured['statistics']:
        summaries.setdefault(row['metric'], []).append(row)
    contract = {'row_count': len(bound['rows']), 'column_indices': dict(zip(columns, range(len(columns)))),
                'requested_columns': list(spec['columns']),
                'group_by': group_by, 'group_order': group_order, 'groups': groups,
                'summary_series': summaries,
                'missing_values': 'Exclude null per metric or coordinate pair; never replace with zero.'}
    figure = {**copy.deepcopy(spec), 'columns': columns, 'data_contract': contract}
    extra_categories = [i for i, name in enumerate(columns) if name != group_by
                        and any(isinstance(row[i], str) for row in bound['rows'])]
    if figure.get('caption') and summaries and not extra_categories:
        counts = '; '.join(metric + ': ' + ', '.join(f'{row["group"]} n={row["count"]}' for row in entries)
                           for metric, entries in summaries.items())
        suffix = ' Valid input observations by analysis group — ' + counts + '.'
        if len(figure['caption'] + suffix) <= 3000:
            figure['caption'] += suffix
        else:
            figure['caption'] += ' See the analysis table for valid sample counts.'
    description = metadata.get('description')
    if description:
        # Preserve provenance verbatim: the model must not silently drop a synthetic-data disclosure.
        figure['caption'] = (figure.get('caption', '') + '\nData description (provided): ' + description).strip()
    return bound, measured, figure
