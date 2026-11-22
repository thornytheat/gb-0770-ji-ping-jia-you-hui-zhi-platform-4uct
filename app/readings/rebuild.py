from decimal import Decimal

from app.diagnostics import detail
from .delta import difference


def rebuild(selection):
    lines = []
    for record in selection:
        fault,liters = record['fault'],None
        handover,opened = record.get('handover'),record.get('opened_version')
        if not fault:
            liters,reason = difference(record['ticket_record'],record['version'],opened,handover)
            if reason:
                fault = detail(reason)
        method = None
        if not fault:
            method = 'handover' if handover else record['ticket_record']['event']
        lines.append(dict(record,fault=fault,volume_l=format(liters,'f') if liters is not None else None,
                          method=method,
                          basis=('handover_full_scale' if handover else 'meter_delta') if not fault else None,
                          handover_l=(format(Decimal(handover['old_l']),'f'),
                                      format(Decimal(handover['new_l']),'f')) if handover else None))
    return {'rows':lines,'reconstructed':sum(row['fault'] is None for row in lines),
            'rollovers':sum(row['method']=='wrap' for row in lines),
            'handovers':sum(row['method']=='handover' for row in lines),
            'held':sum(row['fault'] is not None for row in lines)}
