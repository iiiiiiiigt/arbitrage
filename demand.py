"""Demand from verified, complete daily variant totals; never from listing averages."""
from datetime import datetime, timedelta, timezone
from statistics import median
import math

def weekly_demand(record, today=None, allow_stale=False):
    unknown = {'median_daily': None, 'weekly_total': None,
               'reason': 'Verified seven-day variant sales history unavailable'}
    if not record or record.get('demand_scope') not in ('variant_daily_totals', 'observed_product_daily_totals'):
        return unknown
    today = today or datetime.now(timezone.utc).date()
    expected = {(today-timedelta(days=i)).isoformat() for i in range(1,8)}
    stale = False
    if allow_stale:
        try:
            dates = [datetime.fromisoformat(r['date']).date() for r in record.get('daily_sales', [])]
            latest = max(dates)
            if latest < today-timedelta(days=1):
                expected = {(latest-timedelta(days=i)).isoformat() for i in range(7)}
                stale = True
        except (ValueError, TypeError, KeyError, AttributeError):
            return unknown
    totals = {}
    for row in record.get('daily_sales', []):
        if not isinstance(row, dict):
            return unknown
        day = row.get('date')
        if day not in expected:
            continue
        count = row.get('count')
        if day in totals or isinstance(count, bool) or not isinstance(count, (int,float)):
            return unknown
        if not math.isfinite(count) or count < 0 or int(count) != count:
            return unknown
        totals[day] = int(count)
    if set(totals) != expected:
        return unknown
    return {'median_daily': float(median(totals.values())),
            'weekly_total': sum(totals.values()), 'reason': None,
            'stale': stale, 'checked_at': record.get('fetched_at'),
            'period_start': min(expected), 'period_end': max(expected),
            'coverage': record.get('coverage'),
            'observed_only': record.get('demand_scope') == 'observed_product_daily_totals'}
