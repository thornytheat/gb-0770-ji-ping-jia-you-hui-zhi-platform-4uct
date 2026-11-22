from collections import Counter, defaultdict

from app.diagnostics import RampError, detail
from .handover import handover_report
from .reader import rows_from
from .schemas import COLUMNS, TICKET_HANDOVER_FIELDS, convert_fields

OPTIONAL = {'tickets':TICKET_HANDOVER_FIELDS}


def channel_report(channel, raw):
    groups = defaultdict(list)
    rejected, repeated = [], []
    count, segments = 0,1
    for line,header,row in rows_from(raw,COLUMNS[channel],OPTIONAL.get(channel,())):
        if row==header:
            segments += 1
            continue
        count += 1
        try:
            if not any(field.strip() for field in row):
                raise ValueError('BLANK_ROW')
            if len(row)!=len(header):
                raise ValueError('WIDTH')
            data = convert_fields({key:value.strip() for key,value in zip(header,row)},channel)
            identity = (data['truck'],data['movement']) if channel=='stock' else data['ticket']
            groups[identity].append({'line':line,'data':data,'raw':row})
        except ValueError as exc:
            origin = dict(zip(header,row)) if len(row)==len(header) else {}
            rejected.append(dict(detail(str(exc)),line=line,raw=row,
                                 truck=origin.get('truck','').strip() or None))
    if not count:
        raise RampError('HEADER_ONLY')
    kept = []
    for group in groups.values():
        first,*rest = group
        if any(item['data']!=first['data'] for item in rest):
            related = [item['line'] for item in group]
            rejected.extend(dict(detail('CONFLICT'),line=r['line'],raw=r['raw'],data=r['data'],related=related)
                            for r in group)
        else:
            kept.append(first)
            repeated.extend({'line':item['line'],'kept':first['line']} for item in rest)
    return {'input':count,'accepted':len(kept),'isolated':len(rejected),'duplicate':len(repeated),'segments':segments,
            'rows':kept,'quarantine':sorted(rejected,key=lambda e:e['line']),'repeat_sources':repeated,
            'reasons':dict(Counter(row['code'] for row in rejected))}


def embedded_handovers(sheet):
    """从计量票的可选尾列取出交接读数；坏值按票留痕，不污染票据原件。"""
    records, broken = [], []
    for item in sheet['rows']:
        data = item['data']
        record = data.pop('handover',None)
        error = data.pop('handover_error',None)
        if record is not None:
            records.append({'line':item['line'],'data':record,'raw':item['raw']})
        if error:
            broken.append(dict(detail(error),line=item['line'],raw=item['raw'],
                               truck=data['truck'],ticket=data['ticket']))
    return records,broken


def _content(row):
    data = row['data']
    return (data['truck'],data.get('handover_at'),data['old_l'],data['new_l'],
            data.get('old_range_l'),data.get('new_range_l'))


def _merge_into(table, records, broken):
    """票据尾列与独立交接表都算原件。

    同一次换表在两处各记一遍只算一次重复；同票异值、一票多条交接、或同一条读数
    挂到两张票上，作为冲突整组隔离，原件行号全部保留。
    """
    entries = [{'line':row['line'],'data':row['data'],'raw':row.get('raw',[])}
               for row in list(table.get('rows',[]))+records]
    content_groups = defaultdict(list)
    for entry in entries:
        content_groups[_content(entry)].append(entry)
    survivors, repeated, conflicts = [], [], []
    for group in content_groups.values():
        tickets = {entry['data'].get('ticket') for entry in group if entry['data'].get('ticket')}
        if len(tickets) > 1:
            conflicts.append(group)
            continue
        ordered = sorted(group,key=lambda entry:(entry['data'].get('ticket') is None,entry['line']))
        survivors.append(ordered[0])
        repeated.extend({'line':entry['line'],'kept':ordered[0]['line']} for entry in ordered[1:])
    ticket_groups = defaultdict(list)
    for entry in survivors:
        ticket_groups[(entry['data']['truck'],entry['data'].get('ticket'))].append(entry)
    kept = []
    for group in ticket_groups.values():
        first = group[0]
        if all(entry['data']==first['data'] for entry in group[1:]):
            kept.append(first)
            repeated.extend({'line':entry['line'],'kept':first['line']} for entry in group[1:])
        else:
            conflicts.append(group)
    quarantined = []
    for group in conflicts:
        related = [entry['line'] for entry in group]
        quarantined.extend(dict(detail('CONFLICT'),line=entry['line'],raw=entry['raw'],data=entry['data'],
                                truck=entry['data']['truck'],ticket=entry['data'].get('ticket'),
                                handover_at=entry['data'].get('handover_at'),related=related)
                           for entry in group)
    quarantine = sorted(list(table.get('quarantine',[]))+broken+quarantined,key=lambda e:e['line'])
    by_line = {row['line']:row['kept'] for row in table.get('repeat_sources',[])}
    for row in repeated:
        by_line.setdefault(row['line'],row['kept'])
    return {'input':table.get('input',0)+len(records)+len(broken),
            'accepted':len(kept),'isolated':len(quarantine),'duplicate':len(by_line),
            'segments':table.get('segments',0) or 1,
            'rows':kept,'quarantine':quarantine,
            'repeat_sources':[{'line':line,'kept':kept_line} for line,kept_line in sorted(by_line.items())],
            'reasons':dict(Counter(row['code'] for row in quarantine)),
            'held_trucks':sorted({row['truck'] for row in quarantine if row.get('truck')}),
            'broken':[{'truck':row.get('truck'),'ticket':row.get('ticket'),'handover_at':row.get('handover_at'),
                       'line':row['line'],'code':row['code']} for row in quarantine if row.get('truck')],
            'by_ticket':sorted({row['data']['ticket'] for row in kept if row['data'].get('ticket')}),
            'provided':table.get('provided') or bool(records) or bool(broken)}


def admit(files):
    sheets = {name:channel_report(name,files[name]) for name in COLUMNS}
    table = handover_report(files.get('handovers'))
    records,broken = embedded_handovers(sheets['tickets'])
    handovers = _merge_into(table,records,broken) if (records or broken) else table
    totals = {key:sum(sheet[key] for sheet in sheets.values()) for key in ('input','accepted','isolated','duplicate')}
    return dict(totals,sheets=sheets,handovers=handovers,
                row_balance=totals['input']==totals['accepted']+totals['isolated']+totals['duplicate'])
