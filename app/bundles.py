from flask import request
from app.configuration import SAMPLE_PATH
from app.diagnostics import RampError
from app.intake.schemas import COLUMNS

REQUIRED_KEYS = set(COLUMNS)
OPTIONAL_KEYS = {'handovers','handover','swaps','meter_handovers'}
SAMPLES = ('clean','dirty','swap')


def sample_set(name):
    if name not in SAMPLES:
        raise RampError('SAMPLE',404)
    files = {key:(SAMPLE_PATH/f'{name}-{key}.csv').read_bytes() for key in COLUMNS}
    handovers = SAMPLE_PATH/f'{name}-handovers.csv'
    if handovers.exists():
        files['handovers'] = handovers.read_bytes()
    return files


def submitted():
    if request.method=='GET':
        return sample_set(request.args.get('sample','dirty'))
    body = request.get_json(silent=True)
    if isinstance(body,dict) and set(body)=={'sample'} and isinstance(body['sample'],str):
        return sample_set(body['sample'])
    if not (isinstance(body,dict) and REQUIRED_KEYS <= set(body) and set(body) <= REQUIRED_KEYS | OPTIONAL_KEYS
            and all(isinstance(value,str) for value in body.values())):
        raise RampError('BUNDLE')
    if not body.get('handovers'):
        for alias in OPTIONAL_KEYS - {'handovers'}:
            if body.get(alias):
                body = dict(body,handovers=body[alias])
                break
    try:
        return {key:value.encode() for key,value in body.items() if key in REQUIRED_KEYS | {'handovers'}}
    except UnicodeEncodeError as exc:
        raise RampError('FILE') from exc
