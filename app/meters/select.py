from collections import defaultdict
from decimal import Decimal

from app.diagnostics import detail
from app.intake.handover import index_handovers, resolve, trips_swapped


def candidates(ranges, at):
    return [row for row in ranges if row['since']<=at<row['until']]


def winner(options):
    if not options:
        return None,'NO_VERSION'
    highest = max(row['revision'] for row in options)
    chosen = [row for row in options if row['revision']==highest]
    return (chosen[0],None) if len(chosen)==1 else (None,'VERSION_TIE')


def _pick(ranges, at):
    return winner(candidates(ranges,at))


def _crossing_versions(ranges, start, end, boundaries, handover):
    """跨表票：启读按旧表解释、止读按新表解释，票内实际量程不得多于两个。

    换表前后的量程可以是同一版本（同型号同量程换表），只要有交接读数写明接续点。
    """
    opened,reason = _pick(ranges,start)
    if reason:
        return None,None,reason
    closing,reason = _pick(ranges,end)
    if reason:
        return None,None,reason
    for at in boundaries:
        _,at_reason = _pick(ranges,at)
        if at_reason:
            return None,None,at_reason
    touched = {row['id'] for row in (opened,closing)}
    for at in boundaries:
        touched.update(row['id'] for row in candidates(ranges,at))
    if len(touched) > 2:
        return None,None,'VERSION_SPLIT'
    for key,version in (('old_range_l',opened),('new_range_l',closing)):
        claimed = handover.get(key)
        if claimed is not None and Decimal(claimed) != Decimal(version['range_l']):
            return None,None,'HANDOVER_RANGE'
    return opened,closing,None


def select_versions(tickets, versions, handovers=None):
    trucks = defaultdict(list)
    for row in versions:
        trucks[row['truck']].append(row)
    events, by_ticket, issues = index_handovers(handovers)
    rows = []
    for ticket in tickets:
        info = ticket['data']
        ranges = trucks[info['truck']]
        start,end = info['opened'],info['closed']
        first = candidates(ranges,start)
        selection,reason = winner(first)
        boundaries = sorted({row[key] for row in ranges for key in ('since','until') if start < row[key] <= end})
        touched = {row['id'] for row in first}
        for at in boundaries:
            options = candidates(ranges,at)
            candidate,_ = winner(options)
            touched.update(row['id'] for row in options)
            if selection and candidate!=selection:
                reason = 'VERSION_SPLIT'
        handover,handover_fault = resolve(events,by_ticket,issues,info['ticket'],info['truck'],start,end)
        opened_version = None
        if handover_fault:
            reason = handover_fault
        elif handover:
            opened_version,closing,reason = _crossing_versions(ranges,start,end,boundaries,handover)
            if not reason:
                selection = closing
                touched.update(row['id'] for row in (opened_version,closing))
        elif reason=='VERSION_SPLIT' and trips_swapped(handovers,info['truck']):
            # 当班确有换表，但本票没有可用的交接读数：跨表票留待核。
            reason = 'HANDOVER_MISSING'
        crossing = bool(handover)
        fault = detail(reason) if reason else None
        rows.append({'ticket':info['ticket'],'truck':info['truck'],'line':ticket['line'],'ticket_record':info,
                     'opened_candidates':sorted(row['id'] for row in first),'touched':sorted(touched),
                     'version':selection if not fault else None,
                     'opened_version':opened_version if crossing and not fault else None,
                     'handover':handover if crossing and not fault else None,
                     'handover_crossing':crossing,
                     'handover_fault':fault if crossing or (fault and fault['code'].startswith('HANDOVER')) else None,
                     'fault':fault})
    return {'rows':rows,'selected':sum(row['fault'] is None for row in rows),
            'held':sum(row['fault'] is not None for row in rows),
            'overlaps':sum(len(row['opened_candidates'])>1 for row in rows),
            'handover_crossings':sum(row['handover'] is not None for row in rows),
            'handover_held':sum(row['handover_fault'] is not None for row in rows)}
