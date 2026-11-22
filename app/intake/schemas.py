from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re

COLUMNS = {
    'tickets':('ticket','truck','opened','closed','start_l','stop_l','event','fuel','rho','rho_unit','temp_c'),
    'receipts':('ticket','truck','signed_kg','document'),
    'stock':('truck','movement','kind','kg'),
}
# 换表交接读数既可单独成表，也可作为计量票的可选尾列。尾列只允许下列字段。
TICKET_HANDOVER_FIELDS = ('handover_at','old_l','new_l','old_range_l','new_range_l','handover_document')
EMPTY_MARKERS = {'','-','N/A','NULL','未知'}
DECIMAL_TEXT = re.compile(r'[+-]?[0-9]{1,10}(?:\.[0-9]{1,6})?')


def amount(text, *, signed=False, exact_grams=False):
    if not DECIMAL_TEXT.fullmatch(text):
        raise ValueError
    value = Decimal(text)
    if (not signed and value<0) or (exact_grams and value*1000 != (value*1000).to_integral_value()):
        raise ValueError
    return value


def iso_utc(text):
    at = datetime.fromisoformat(text)
    if at.utcoffset() is None:
        raise ValueError
    return at.astimezone(timezone.utc).isoformat(timespec='microseconds')


def handover_fields(row):
    """解析计量票上的交接尾列。返回 (交接读数, 故障码)；都没有则为 (None, None)。"""
    present = {key:row.get(key,'').strip() for key in TICKET_HANDOVER_FIELDS}
    if not any(value not in EMPTY_MARKERS for value in present.values()):
        return None,None
    if any(present[key] in EMPTY_MARKERS for key in ('handover_at','old_l','new_l')):
        return None,'HANDOVER_MISSING'
    try:
        record = {'truck':row['truck'],'ticket':row['ticket'],
                  'handover_at':iso_utc(present['handover_at'])}
        for key in ('old_l','new_l'):
            record[key] = format(amount(present[key]).normalize(),'f')
        for key in ('old_range_l','new_range_l'):
            text = present[key]
            if text in EMPTY_MARKERS:
                record[key] = None
            else:
                value = amount(text)
                if value <= 0:
                    return None,'HANDOVER_MISSING'
                record[key] = format(value.normalize(),'f')
    except (InvalidOperation,ValueError,OverflowError):
        return None,'HANDOVER_MISSING'
    document = present['handover_document']
    record['document'] = '' if document in EMPTY_MARKERS else document
    return record,None


def convert_fields(row, channel):
    if any(value in EMPTY_MARKERS for value in (row[key] for key in COLUMNS[channel] if key in row)):
        raise ValueError('SENTINEL')
    try:
        if channel == 'tickets':
            for key in ('opened','closed'):
                row[key] = iso_utc(row[key])
            if row['closed']<=row['opened'] or row['event'] not in ('normal','wrap','reset'):
                raise ValueError
            for key in ('start_l','stop_l','rho','temp_c'):
                row[key] = format(amount(row[key],signed=key=='temp_c').normalize(),'f')
            handover,error = handover_fields(row)
            if handover is not None:
                row['handover'] = handover
            if error:
                row['handover_error'] = error
        elif channel == 'receipts':
            row['signed_g'] = int(amount(row.pop('signed_kg'),exact_grams=True)*1000)
        else:
            if row['kind'] not in ('opening','inbound','closing'):
                raise ValueError
            row['mass_g'] = int(amount(row.pop('kg'),exact_grams=True)*1000)
    except (InvalidOperation,ValueError,OverflowError) as exc:
        raise ValueError('MALFORMED') from exc
    return row
