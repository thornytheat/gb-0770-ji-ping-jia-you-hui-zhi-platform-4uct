import csv
import io

from app.diagnostics import RampError


def decode(raw):
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            text = raw.decode('gb18030')
        except UnicodeDecodeError as exc:
            raise RampError('FILE') from exc
    if text=='':
        raise RampError('EMPTY_FILE')
    if not text.strip():
        raise RampError('BLANK_FILE')
    return text


def rows_from(raw, required, optional=()):
    """逐行给出表头与原件行。分段按表头全等判断；可选列只放行不加必需。"""
    text = decode(raw)
    reader = csv.reader(io.StringIO(text,newline=''),strict=True)
    try:
        header = next(reader)
        if not set(required) <= set(header) or not set(header) <= set(required) | set(optional) \
                or len(set(header)) != len(header):
            raise RampError('HEADER')
        line = reader.line_num+1
        for row in reader:
            yield line,header,row
            line = reader.line_num+1
    except csv.Error as exc:
        raise RampError('FILE') from exc
