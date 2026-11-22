from collections import defaultdict
from app.diagnostics import detail
from app.intake.handover import held_trucks

HANDOVER_CODES = {'HANDOVER_MISSING','HANDOVER_CONFLICT','HANDOVER_RANGE'}
TITLES = {'HANDOVER_CONFLICT':'换表交接记录冲突','HANDOVER_MISSING':'跨表票缺少换表交接读数',
          'HANDOVER_HOLD':'换表交接待核'}


def conservation(stock, findings, handover=None):
    holding = defaultdict(lambda:defaultdict(list))
    for item in stock['rows']:
        row = item['data']
        holding[row['truck']][row['kind']].append(item)
    held_positions = defaultdict(list)
    for item in stock['quarantine']:
        truck = item['data']['truck'] if 'data' in item else item.get('truck')
        if truck:
            held_positions[truck].append(item['line'])
    reviewed = held_trucks(handover)
    issued = defaultdict(int)
    handover_mass = defaultdict(int)
    handover_tickets = defaultdict(list)
    handover_holds = defaultdict(set)
    for finding in findings:
        issued[finding['truck']] += finding['meter_g'] or 0
        if finding.get('basis')=='handover_full_scale' and finding['meter_g'] is not None:
            handover_mass[finding['truck']] += finding['meter_g']
            handover_tickets[finding['truck']].append(finding['ticket'])
        code = (finding.get('fault') or {}).get('code')
        if code in HANDOVER_CODES or (finding.get('handover_crossing') and finding.get('fault')):
            handover_holds[finding['truck']].add(code or 'HANDOVER_HOLD')
    result = []
    known = set(holding) | set(issued)
    pending_trucks = (reviewed & known) | set(handover_holds)
    for truck in sorted(known | set(held_positions) | pending_trucks):
        entries = holding[truck]
        opening,closing = entries['opening'],entries['closing']
        inbound = sum(row['data']['mass_g'] for row in entries['inbound'])
        issue = issued[truck]
        pending = truck in pending_trucks
        complete = len(opening)==len(closing)==1 and not held_positions[truck] and not pending
        start = opening[0]['data']['mass_g'] if len(opening)==1 else None
        end = closing[0]['data']['mass_g'] if len(closing)==1 else None
        residual = start+inbound-issue-end if complete else None
        if pending:
            codes = handover_holds[truck] or {'HANDOVER_HOLD'}
            code = ('HANDOVER_CONFLICT' if 'HANDOVER_CONFLICT' in codes else
                    'HANDOVER_MISSING' if 'HANDOVER_MISSING' in codes else 'HANDOVER_HOLD')
            fault = dict(detail(code),title=TITLES[code],crossing_codes=sorted(codes))
        elif not complete:
            fault = detail('STOCK_ANCHORS')
        else:
            fault = None
        entry = {'truck':truck,'opening_g':start,'inbound_g':inbound,'issued_g':issue,'closing_g':end,
                 'difference_g':residual,'complete':complete,'fault':fault,
                 'handover_g':handover_mass[truck],'handover_tickets':sorted(handover_tickets[truck]),
                 'handover_pending':pending,
                 'source_lines':sorted(item['line'] for group in entries.values() for item in group),
                 'isolated_lines':held_positions[truck]}
        if pending:
            # 受影响的车单列可信计量量，差额留待核，不把换表差计入库存损耗。
            entry['pending_g'] = start+inbound-issue-end if None not in (start,end) else None
        result.append(entry)
    return result
