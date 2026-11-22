from decimal import Decimal


def _segment_for(opening, closing, maximum, band, declared):
    """单段读数解释：返回 (升数, 是否按回卷计算, 故障码)。"""
    if closing >= opening:
        return closing-opening,False,None
    if not declared:
        return None,False,'REVERSE'
    if opening < maximum-band or closing >= band:
        return None,False,'BAD_WRAP'
    return maximum-opening+closing,True,None


def _declared_mismatch(handover, opened, closing):
    for key,version in (('old_range_l',opened),('new_range_l',closing)):
        claimed = handover.get(key)
        if claimed is not None and Decimal(claimed) != Decimal(version['range_l']):
            return True
    return False


def _crossing(ticket, opened, closing, handover):
    """跨表票：启读至旧表交接读数、新表起始读数至止读，两段实测相加。

    交接读数已经把接续点写实，因此每段先按正差解释；只有该段止读小于启读、
    且票面声明回卷时，才按该段量程回卷计算。
    """
    start,stop = Decimal(ticket['start_l']),Decimal(ticket['stop_l'])
    old_cut,new_start = Decimal(handover['old_l']),Decimal(handover['new_l'])
    old_max,new_max = Decimal(opened['range_l']),Decimal(closing['range_l'])
    if not (0 <= start < old_max and 0 <= old_cut < old_max and
            0 <= new_start < new_max and 0 <= stop < new_max):
        return None,'HANDOVER_RANGE'
    if _declared_mismatch(handover,opened,closing):
        return None,'HANDOVER_RANGE'
    declared = ticket['event']=='wrap'
    old_liters,old_wrap,reason = _segment_for(start,old_cut,old_max,Decimal(opened['wrap_band_l']),declared)
    if reason:
        return None,reason
    new_liters,new_wrap,reason = _segment_for(new_start,stop,new_max,Decimal(closing['wrap_band_l']),declared)
    if reason:
        return None,reason
    if old_wrap and new_wrap:
        # 模型只支持一次回卷，两段都回卷无法用一张交接单解释。
        return None,'BAD_WRAP'
    liters = old_liters+new_liters
    cap = max(Decimal(opened['max_ticket_l']),Decimal(closing['max_ticket_l']))
    if liters > cap:
        return None,'DECLARED_LIMIT'
    return liters,None


def difference(ticket, version, opened_version=None, handover=None):
    opening, closing = Decimal(ticket['start_l']), Decimal(ticket['stop_l'])
    if handover is not None and opened_version is not None:
        if ticket['event']=='reset':
            return None,'RESET'
        return _crossing(ticket,opened_version,version,handover)
    maximum = Decimal(version['range_l'])
    if not 0 <= opening < maximum or not 0 <= closing < maximum:
        return None,'METER_BOUNDS'
    if ticket['event']=='reset':
        return None,'RESET'
    if ticket['event']=='wrap':
        band = Decimal(version['wrap_band_l'])
        if opening < maximum-band or closing >= band or closing >= opening:
            return None,'BAD_WRAP'
        liters = maximum - opening + closing
    else:
        liters = closing-opening
        if liters < 0:
            return None,'REVERSE'
    if liters > Decimal(version['max_ticket_l']):
        return None,'DECLARED_LIMIT'
    return liters,None
