from decimal import Decimal

from app.bundles import sample_set
from app.intake.evidence import admit
from app.intake.handover import handover_report
from app.readings.delta import difference
from app.shift import compute


TICKETS = ('ticket,truck,opened,closed,start_l,stop_l,event,fuel,rho,rho_unit,temp_c\n'
           'A,FT-12,2026-11-22T01:40:00Z,2026-11-22T02:10:00Z,950,40,normal,JET-A1,800,kg/m3,15\n'
           'B,FT-12,2026-11-22T03:00:00Z,2026-11-22T03:10:00Z,50,60,normal,JET-A1,800,kg/m3,15\n')
RECEIPTS = 'ticket,truck,signed_kg,document\nA,FT-12,40,R1\nB,FT-12,8,R2\n'
STOCK = 'truck,movement,kind,kg\nFT-12,O,opening,300\nFT-12,C,closing,252\n'
SWAP = 'truck,ticket,handover_at,old_l,new_l,document\nFT-12,A,2026-11-22T01:55:00Z,980,20,H1\n'


def bundle(handovers=None):
    files = {'tickets':TICKETS.encode(),'receipts':RECEIPTS.encode(),'stock':STOCK.encode()}
    if handovers is not None:
        files['handovers'] = handovers.encode()
    return files


def reading(result, ticket):
    return next(row for row in result['readings']['rows'] if row['ticket']==ticket)


def stock(result, truck='FT-12'):
    return next(row for row in result['balance']['stocks'] if row['truck']==truck)


def test_swap_sample_reconciles_with_handover_readings():
    out = compute(sample_set('swap'))
    assert reading(out,'A01')['volume_l']=='50' and reading(out,'A01')['method']=='handover'
    assert out['balance']['totals']['difference_g']==0
    assert [row['issued_g'] for row in out['balance']['stocks']]==[40000,40000,16000]


def test_mixed_readings_without_handover_do_not_become_stock_loss():
    blind = compute(bundle())
    assert reading(blind,'A')['fault']['code']=='VERSION_SPLIT'
    assert stock(blind)['issued_g']==8000
    # 旧表 950 L 与新表 40 L 直接相减得到的负数会被当成库存损耗。
    assert stock(blind)['difference_g']==40000
    metered = compute(bundle(SWAP))
    assert stock(metered)['difference_g']==0
    assert stock(metered)['handover_g']==40000


def test_crossing_ticket_uses_measured_handover_segments():
    out = compute(bundle(SWAP))
    row = reading(out,'A')
    assert (row['volume_l'],row['basis'],row['handover_l'])==('50','handover_full_scale',('980','20'))
    assert row['version']['id']=='F12-2' and row['opened_version']['id']=='F12-1'
    mass = next(item for item in out['mass']['rows'] if item['ticket']=='A')
    assert mass['standard']['mass_g']==40000


def test_ticket_that_does_not_cross_is_not_treated_as_swap():
    out = compute(bundle(SWAP))
    assert reading(out,'B')['method']=='normal' and reading(out,'B')['volume_l']=='10'


def test_embedded_handover_columns_match_separate_sheet():
    embedded = TICKETS.replace('temp_c\n','temp_c,handover_at,old_l,new_l,handover_document\n') \
        .replace('15\n','15,2026-11-22T01:55:00Z,980,20,H1\n',1) \
        .replace('15\n','15,,,\n',1)
    files = {'tickets':embedded.encode(),'receipts':RECEIPTS.encode(),'stock':STOCK.encode()}
    out = compute(files)
    assert reading(out,'A')['volume_l']=='50' and reading(out,'A')['handover']['document']=='H1'
    assert out['intake']['handovers']['accepted']==1


def test_missing_handover_reading_holds_only_affected_truck():
    # 交接单只写车辆与时刻，旧表止读缺失：跨表票留待核，另一辆车照常对账。
    files = bundle()
    files['tickets'] = ('ticket,truck,opened,closed,start_l,stop_l,event,fuel,rho,rho_unit,temp_c\n'
                        'A,FT-12,2026-11-22T01:40:00Z,2026-11-22T02:10:00Z,950,40,normal,JET-A1,800,kg/m3,15\n'
                        'C,FT-18,2026-11-22T01:10:00Z,2026-11-22T01:20:00Z,10,60,normal,JET-A1,800,kg/m3,15\n').encode()
    files['receipts'] = ('ticket,truck,signed_kg,document\nA,FT-12,40,R1\nC,FT-18,40,R3\n').encode()
    files['stock'] = ('truck,movement,kind,kg\nFT-12,O,opening,300\nFT-12,C,closing,252\n'
                      'FT-18,O2,opening,140\nFT-18,C2,closing,100\n').encode()
    files['handovers'] = ('truck,handover_at,old_l,new_l,document\n'
                          'FT-12,2026-11-22T01:55:00Z,-,20,H1\n').encode()
    out = compute(files)
    held, ok = stock(out), stock(out,'FT-18')
    assert held['complete'] is False and held['fault']['code']=='HANDOVER_MISSING'
    assert held['difference_g'] is None and held['pending_g']==48000
    assert ok['complete'] is True and ok['difference_g']==0
    assert out['balance']['totals']['difference_g']==0
    assert out['balance']['handover_pending_trucks']==['FT-12']


def test_conflicting_handover_rows_are_quarantined_and_truck_held():
    clash = SWAP+'FT-12,A,2026-11-22T01:55:00Z,981,20,H1b\n'
    out = compute(bundle(clash))
    sheet = out['intake']['handovers']
    assert sheet['isolated']==2 and [row['code'] for row in sheet['quarantine']]==['CONFLICT','CONFLICT']
    assert sheet['quarantine'][0]['related']==[2,3]
    assert reading(out,'A')['fault']['code']=='HANDOVER_CONFLICT'
    assert stock(out)['complete'] is False and stock(out)['difference_g'] is None


def test_handover_declared_range_must_match_version():
    claimed = 'truck,ticket,handover_at,old_l,new_l,old_range_l,new_range_l,document\n' \
              'FT-12,A,2026-11-22T01:55:00Z,980,20,500,1000,H1\n'
    out = compute(bundle(claimed))
    assert reading(out,'A')['fault']['code']=='HANDOVER_RANGE'


def test_handover_reading_outside_meter_bounds_is_held():
    bent = 'truck,ticket,handover_at,old_l,new_l,document\nFT-12,A,2026-11-22T01:55:00Z,1200,20,H1\n'
    out = compute(bundle(bent))
    assert reading(out,'A')['fault']['code']=='HANDOVER_RANGE'


def test_replayed_batch_records_measurement_basis(client,ramp):
    files = {key:value.decode() for key,value in bundle(SWAP).items()}
    batch = client.post('/api/shifts',json=files).json['batch']
    assert client.post(f'/api/shifts/{batch}/sign').status_code==200
    slip = client.get(f'/api/shifts/{batch}/export').json['slip']
    basis = next(row for row in slip['metering_basis'] if row['ticket']=='A')
    assert (basis['basis'],basis['version'],basis['opened_version'])==('handover_full_scale','F12-2','F12-1')
    assert slip['handover_sources']['accepted']==1 and slip['handover_sources']['rows'][0]['data']['document']=='H1'
    again = client.post('/api/shifts',json=files)
    assert (again.status_code,again.json['batch'])==(200,batch)


def test_duplicate_handover_in_both_sources_counts_once():
    files = bundle(SWAP)
    files['tickets'] = TICKETS.replace('temp_c\n','temp_c,handover_at,old_l,new_l,handover_document\n') \
        .replace('15\n','15,2026-11-22T01:55:00Z,980,20,H1\n',1) \
        .replace('15\n','15,,,\n',1).encode()
    sheet = admit(files)['handovers']
    assert (sheet['accepted'],sheet['duplicate'],sheet['isolated'])==(1,1,0)


def test_legacy_tickets_without_dash_placeholders():
    files = bundle()
    out = compute(files)
    assert out['intake']['handovers']['provided'] is False
    assert out['versions']['handover_crossings']==0


def test_crossing_wrap_uses_handover_cut_point():
    ticket = {'start_l':'950','stop_l':'40','event':'wrap'}
    opened = {'range_l':'1000','wrap_band_l':'100','max_ticket_l':'300'}
    closing = {'range_l':'1000','wrap_band_l':'50','max_ticket_l':'600'}
    handover = {'old_l':'980','new_l':'20','old_range_l':None,'new_range_l':None}
    assert difference(ticket,closing,opened,handover)==(Decimal('50'),None)
    # 新表段止读小于起读且不满足回卷带：即使票面声明回卷也不得硬凑。
    assert difference(dict(ticket,stop_l='10'),closing,opened,handover)==(None,'BAD_WRAP')
    # 两段都按正差时声明回卷不影响结果，仍取实测接续点。
    assert difference(dict(ticket,start_l='900',stop_l='60'),closing,opened,handover)==(Decimal('120'),None)


def test_handover_sheet_aliases_and_header_only():
    raw = ('车辆,票号,换表时间,旧表止读,新表起读,交接单\n'
           'FT-12,A,2026-11-22T01:55:00Z,980,20,H1\n').encode('gb18030')
    sheet = handover_report(raw)
    assert sheet['accepted']==1 and sheet['rows'][0]['data']['truck']=='FT-12'
    empty = handover_report('truck,ticket,handover_at,old_l,new_l\n'.encode())
    assert empty['provided'] is True and empty['accepted']==0


def test_handover_bundle_requires_strings(client):
    reply = client.post('/api/intake',json={'tickets':'a','receipts':'b','stock':'c','handovers':1})
    assert (reply.status_code,reply.json['error'])==(422,'BUNDLE')


def test_same_meter_swap_still_needs_handover_cut():
    # 同型号同量程换表：选版不跨版本，但读数确实断开，仍要凭交接读数复算。
    tickets = ('ticket,truck,opened,closed,start_l,stop_l,event,fuel,rho,rho_unit,temp_c\n'
               'Q,FT-23,2026-11-22T00:20:00Z,2026-11-22T00:30:00Z,30,50,normal,JET-A1,800,kg/m3,15\n')
    files = {'tickets':tickets.encode(),'receipts':b'ticket,truck,signed_kg,document\nQ,FT-23,16,R1\n',
             'stock':b'truck,movement,kind,kg\nFT-23,O,opening,90\nFT-23,C,closing,42\n'}
    blind = compute(dict(files))
    assert next(r for r in blind['readings']['rows'] if r['ticket']=='Q')['volume_l']=='20'
    cut = dict(files,handovers=b'truck,handover_at,old_l,new_l\nFT-23,2026-11-22T00:25:00Z,40,0\n')
    out = compute(cut)
    row = next(r for r in out['readings']['rows'] if r['ticket']=='Q')
    assert (row['volume_l'],row['method'])==('60','handover')
    assert out['balance']['stocks'][0]['difference_g']==0


def test_ticket_tail_missing_reading_holds_ticket_not_bundle():
    tickets = TICKETS.replace('temp_c\n','temp_c,handover_at,old_l,new_l\n') \
        .replace('15\n','15,2026-11-22T01:55:00Z,-,20\n',1) \
        .replace('15\n','15,,,\n',1)
    out = compute({'tickets':tickets.encode(),'receipts':RECEIPTS.encode(),'stock':STOCK.encode()})
    assert out['intake']['sheets']['tickets']['accepted']==2
    assert reading(out,'A')['fault']['code']=='HANDOVER_MISSING'
    assert reading(out,'B')['volume_l']=='10'
    assert stock(out)['complete'] is False


def test_swap_sample_ships_its_handover_sheet():
    files = sample_set('swap')
    assert 'handovers' in files and b'A01' in files['handovers']


def test_only_one_rollover_across_two_segments():
    version = {'range_l':'1000','wrap_band_l':'100','max_ticket_l':'300'}
    handover = {'old_l':'980','new_l':'20','old_range_l':None,'new_range_l':None}
    # 旧表段 990→980 回卷、新表段 20→10 也回卷：模型只支持一次回卷，留待核。
    double = {'start_l':'990','stop_l':'10','event':'wrap'}
    assert difference(double,version,version,handover)==(None,'BAD_WRAP')
    single = {'start_l':'950','stop_l':'40','event':'wrap'}
    assert difference(single,version,version,handover)==(Decimal('50'),None)


def test_manual_reset_still_holds_crossing_ticket():
    tickets = ('ticket,truck,opened,closed,start_l,stop_l,event,fuel,rho,rho_unit,temp_c\n'
               'A,FT-12,2026-11-22T01:40:00Z,2026-11-22T02:10:00Z,950,40,reset,JET-A1,800,kg/m3,15\n')
    files = {'tickets':tickets.encode(),'receipts':b'ticket,truck,signed_kg,document\nA,FT-12,40,R1\n',
             'stock':b'truck,movement,kind,kg\nFT-12,O,opening,300\nFT-12,C,closing,252\n',
             'handovers':b'truck,ticket,handover_at,old_l,new_l\nFT-12,A,2026-11-22T01:55:00Z,980,20\n'}
    out = compute(files)
    assert reading(out,'A')['fault']['code']=='RESET'
    assert stock(out)['complete'] is False
