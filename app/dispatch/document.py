def _basis(readings):
    """逐票固定计量依据，供归档后复核换表票用了哪两段实测读数。"""
    return [{'ticket':row['ticket'],'basis':row.get('basis'),'method':row.get('method'),
             'volume_l':row.get('volume_l'),'handover_l':row.get('handover_l'),
             'version':(row.get('version') or {}).get('id'),'opened_version':(row.get('opened_version') or {}).get('id'),
             'handover':row.get('handover'),'ticket_line':row.get('line')}
            for row in readings]


def compile_slip(archive):
    stages = archive['stages']
    handover = stages['intake'].get('handovers') or {}
    return {'document':'机坪加注证据单','manifest':archive['manifest'],
            'conservation':stages['balance'],'counter_checks':stages['mass']['rows'],
            'metering_basis':_basis(stages['readings']['rows']),
            'handover_sources':{'input':handover.get('input',0),'accepted':handover.get('accepted',0),
                                'isolated':handover.get('isolated',0),'rows':handover.get('rows',[]),
                                'quarantine':handover.get('quarantine',[])},
            'sources':stages['intake']['sheets']}
