from collections import Counter, defaultdict
import csv
import io

from app.diagnostics import RampError, detail
from .reader import decode
from .schemas import EMPTY_MARKERS, amount, iso_utc

BASE = ('truck','old_l','new_l')
COLUMNS = ('truck','handover_at','old_l','new_l','old_range_l','new_range_l','ticket','document')
# 读数三件套必须同表出现；只写车辆和凭证不足以复算。
READING_FIELDS = ('old_l','new_l')

ALIASES = {
    'truck':'truck', 'vehicle':'truck', 'car':'truck', 'truck_no':'truck', '车号':'truck', '车辆':'truck',
    'handover_at':'handover_at', 'handover_time':'handover_at', 'swap_at':'handover_at',
    'swap_time':'handover_at', 'swapped_at':'handover_at', 'changed_at':'handover_at',
    'change_at':'handover_at', 'at':'handover_at', 'time':'handover_at',
    '交接时间':'handover_at', '换表时间':'handover_at',
    'old_l':'old_l', 'old_reading':'old_l', 'old_reading_l':'old_l', 'before_l':'old_l',
    'before_reading':'old_l', 'out_l':'old_l', 'outgoing_l':'old_l', 'last_l':'old_l',
    'old_meter_l':'old_l', 'old_reading_value':'old_l', '旧表读数':'old_l', '旧表止读':'old_l',
    'new_l':'new_l', 'new_reading':'new_l', 'new_reading_l':'new_l', 'after_l':'new_l',
    'after_reading':'new_l', 'in_l':'new_l', 'incoming_l':'new_l', 'first_l':'new_l',
    'new_meter_l':'new_l', 'new_reading_value':'new_l', '新表读数':'new_l', '新表起读':'new_l',
    'old_range_l':'old_range_l', 'before_range_l':'old_range_l', 'out_range_l':'old_range_l',
    'old_full_scale_l':'old_range_l', 'old_scale_l':'old_range_l', '旧表量程':'old_range_l',
    'new_range_l':'new_range_l', 'after_range_l':'new_range_l', 'in_range_l':'new_range_l',
    'new_full_scale_l':'new_range_l', 'new_scale_l':'new_range_l', '新表量程':'new_range_l',
    'ticket':'ticket', 'ticket_no':'ticket', 'ticket_id':'ticket', '票号':'ticket', '计量票':'ticket',
    'document':'document', 'doc':'document', 'slip':'document', 'case':'document',
    '交接单':'document', '凭证':'document', '交接凭证':'document',
}


def _canonical(name):
    key = name.strip().lower()
    return ALIASES.get(key,key)


def _empty():
    return {'input':0,'accepted':0,'isolated':0,'duplicate':0,'segments':0,'rows':[],
            'quarantine':[],'repeat_sources':[],'reasons':{},'held_trucks':[],'broken':[],
            'by_ticket':[],'provided':False}


def _origin_tail(origin):
    truck = (origin.get('truck') or '').strip()
    at = (origin.get('handover_at') or '').strip()
    if at in EMPTY_MARKERS:
        at = None
    else:
        try:
            at = iso_utc(at)
        except ValueError:
            at = None
    return truck or None, at


def _convert(origin):
    if any(origin.get(key,'').strip() in EMPTY_MARKERS for key in BASE):
        raise ValueError('SENTINEL')
    ticket = origin.get('ticket','').strip()
    if ticket in EMPTY_MARKERS:
        ticket = None
    at = origin.get('handover_at','').strip()
    if at in EMPTY_MARKERS:
        at = None
    if ticket is None and at is None:
        raise ValueError('SENTINEL')
    data = {'truck':origin['truck'].strip(),'ticket':ticket}
    try:
        data['handover_at'] = iso_utc(at) if at else None
        for key in ('old_l','new_l'):
            data[key] = format(amount(origin[key].strip()).normalize(),'f')
        for key in ('old_range_l','new_range_l'):
            text = origin.get(key,'').strip()
            if text in EMPTY_MARKERS:
                data[key] = None
            else:
                value = amount(text)
                if value <= 0:
                    raise ValueError
                data[key] = format(value.normalize(),'f')
    except ValueError as exc:
        raise ValueError('MALFORMED') from exc
    document = origin.get('document','').strip()
    data['document'] = '' if document in EMPTY_MARKERS else document
    return data


def _identity(data):
    return ('ticket',data['ticket']) if data['ticket'] else ('time',data['truck'],data['handover_at'])


def handover_report(raw):
    """换表交接读数是可选原件；缺省、空白或只剩表头表示本班没有换表。"""
    blank = _empty()
    if raw is None:
        return blank
    if isinstance(raw,str):
        raw = raw.encode()
    try:
        text = decode(raw)
    except RampError as exc:
        if exc.code in ('EMPTY_FILE','BLANK_FILE'):
            return blank
        raise
    reader = csv.reader(io.StringIO(text,newline=''),strict=True)
    groups = defaultdict(list)
    rejected, repeated = [], []
    count, segments = 0, 1
    try:
        raw_header = next(reader)
        header = [_canonical(name) for name in raw_header]
        located = 'ticket' in header or 'handover_at' in header
        if not set(BASE) <= set(header) or len(set(header)) != len(header):
            raise RampError('HEADER')
        if not located:
            # 有读数却没有任何可定位字段：整表无法归票，受影响车辆留待核。
            line = reader.line_num+1
            for row in reader:
                count += 1
                origin = dict(zip(header,row)) if len(row)==len(header) else {}
                truck = (origin.get('truck') or '').strip() or None
                rejected.append(dict(detail('HANDOVER_MISSING'),line=line,raw=row,truck=truck,
                                     handover_at=None,ticket=None))
                line = reader.line_num+1
            return {'input':count,'accepted':0,'isolated':len(rejected),'duplicate':0,
                    'segments':segments,'rows':[],'quarantine':rejected,'repeat_sources':[],
                    'reasons':{'HANDOVER_MISSING':len(rejected)},
                    'held_trucks':sorted({row['truck'] for row in rejected if row['truck']}),
                    'broken':[{'truck':row['truck'],'ticket':None,'handover_at':None,'line':row['line'],
                               'code':'HANDOVER_MISSING'} for row in rejected if row['truck']],
                    'by_ticket':[],'provided':True}
        line = reader.line_num+1
        for row in reader:
            if row == raw_header:
                segments += 1
                continue
            count += 1
            origin = dict(zip(header,row)) if len(row)==len(header) else {}
            truck, at = _origin_tail(origin)
            try:
                if not any(field.strip() for field in row):
                    raise ValueError('BLANK_ROW')
                if len(row) != len(header):
                    raise ValueError('WIDTH')
                data = _convert(origin)
                groups[_identity(data)].append({'line':line,'data':data,'raw':row})
            except ValueError as exc:
                ticket = (origin.get('ticket') or '').strip() or None
                rejected.append(dict(detail(str(exc)),line=line,raw=row,truck=truck,
                                     handover_at=at,ticket=ticket))
            line = reader.line_num+1
    except csv.Error as exc:
        raise RampError('FILE') from exc
    if not count:
        blank.update(segments=segments,provided=True)
        return blank
    kept = []
    for group in groups.values():
        first,*rest = group
        if any(item['data']!=first['data'] for item in rest):
            related = [item['line'] for item in group]
            rejected.extend(dict(detail('CONFLICT'),line=r['line'],raw=r['raw'],data=r['data'],
                                 truck=r['data']['truck'],handover_at=r['data']['handover_at'],
                                 ticket=r['data']['ticket'],related=related)
                            for r in group)
        else:
            kept.append(first)
            repeated.extend({'line':item['line'],'kept':first['line']} for item in rest)
    broken = [{'truck':row['truck'],'ticket':row.get('ticket'),
               'handover_at':row.get('handover_at'),'line':row['line'],'code':row['code']}
              for row in rejected if row['truck']]
    return {'input':count,'accepted':len(kept),'isolated':len(rejected),'duplicate':len(repeated),
            'segments':segments,'rows':kept,'quarantine':sorted(rejected,key=lambda e:e['line']),
            'repeat_sources':repeated,'reasons':dict(Counter(row['code'] for row in rejected)),
            'held_trucks':sorted({row['truck'] for row in broken}),'broken':broken,
            'by_ticket':sorted({row['data']['ticket'] for row in kept if row['data']['ticket']}),
            'provided':True}


def _as_sheet(handovers):
    if handovers is None:
        return _empty()
    if isinstance(handovers,dict) and 'rows' in handovers:
        return handovers
    entries = handovers if isinstance(handovers,list) else []
    rows = [{'line':item.get('line'),'data':dict(item.get('data',item)),'raw':item.get('raw',[])}
            for item in entries]
    return dict(_empty(),rows=rows,provided=True)


def index_handovers(handovers):
    """按车辆与票号整理已采信交接记录，以及无法定位的坏件/冲突行。"""
    sheet = _as_sheet(handovers)
    events, by_ticket, issues = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in sheet.get('rows',[]):
        data = row['data']
        item = dict(data,line=row['line'],raw=row.get('raw',[]))
        events[data['truck']].append(item)
        if data.get('ticket'):
            by_ticket[data['ticket']].append(item)
    for row in sheet.get('quarantine',[]):
        truck = row.get('truck') or (row.get('data') or {}).get('truck')
        if not truck:
            continue
        issues[truck].append({'line':row['line'],'code':row['code'],'ticket':row.get('ticket'),
                              'handover_at':row.get('handover_at')})
    for rows in list(events.values())+list(by_ticket.values()):
        rows.sort(key=lambda item:(item.get('handover_at') or '',item['line'] or 0))
    return events, by_ticket, issues


def resolve(events, by_ticket, issues, ticket, truck, opened, closed):
    """交接读数只作用于对应票：优先票号绑定，其次要求交接时刻落在票的开闭区间内。"""
    def inside(at):
        return at is not None and opened < at < closed

    bound = [item for item in by_ticket.get(ticket,[]) if item['truck']==truck]
    stray = [item for item in by_ticket.get(ticket,[]) if item['truck']!=truck]
    if bound and stray:
        return None,'HANDOVER_CONFLICT'
    if stray:
        return None,'HANDOVER_CONFLICT'
    if len(bound) > 1:
        return None,'HANDOVER_CONFLICT'
    if bound and bound[0].get('handover_at') and not inside(bound[0]['handover_at']):
        # 交接读数挂在本票上，时刻却不在本票开闭区间内：交接记录与票据互相矛盾。
        return None,'HANDOVER_CONFLICT'
    for issue in issues.get(truck,[]):
        if issue.get('ticket')==ticket or (issue.get('ticket') is None and inside(issue['handover_at'])):
            return None,'HANDOVER_CONFLICT' if issue['code']=='CONFLICT' else 'HANDOVER_MISSING'
    if bound:
        return bound[0],None
    window = [item for item in events.get(truck,[]) if not item.get('ticket') and inside(item['handover_at'])]
    if len(window) > 1:
        return None,'HANDOVER_CONFLICT'
    return (window[0],None) if window else (None,None)


def held_trucks(sheet):
    return set((sheet or {}).get('held_trucks',[]))


def trips_swapped(sheet, truck):
    """本班该车是否有换表台账（含坏件）。有台账却查不到交接读数的跨表票留待核。"""
    if not sheet or not sheet.get('provided'):
        return False
    for row in sheet.get('rows',[]):
        if row['data']['truck']==truck:
            return True
    return truck in held_trucks(sheet)
