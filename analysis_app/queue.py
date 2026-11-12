"""Competing-risks analysis on a queue rebuilt at a historical cutoff.

The unit of analysis is a ticket.  For every ticket the queue at ``cutoff``
keeps only the latest record whose ``observed`` time is at or before the
cutoff; records seen later are invisible to that historical run.  The time
origin is ``opened`` and the follow-up time is ``observed - opened``.

Outcomes are treated as competing events: ``complete`` and ``cancel`` both
remove a ticket from the waiting pool, while ``waiting`` (or no event known
yet) is right-censored at the last observation.  Cumulative incidence is
estimated with the Aalen-Johansen estimator, so cancelling is never treated
as ordinary non-informative censoring.
"""

import hashlib
import json
import math
import random
from collections import Counter

from .schema import PRODUCT, UNIT

ALGORITHM = 'latest-known-observation/v1'
DEFINITION = (
    'ticket-level queue: latest record with observed <= cutoff, opened <= cutoff; '
    'outcome as of that record only; later revisions are ignored'
)

COMPLETE = 'complete'
CANCEL = 'cancel'
WAITING = 'waiting'
VALID_EVENTS = {COMPLETE, CANCEL, WAITING}

STATUS_COMPLETE = COMPLETE
STATUS_CANCEL = CANCEL
STATUS_CENSORED = 'censored'

METRICS = ('completion', 'cancellation', 'waiting')


def _finite_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} 必须是有限数值')
    if not math.isfinite(value):
        raise ValueError(f'{name} 必须是有限数值')
    return value


def build_cohort(records, cutoff):
    """Return the ticket cohort known at ``cutoff``.

    Each returned ticket is the latest observation at or before the cutoff.
    Nothing after the cutoff is read, so appending future revisions cannot
    change a historical run.
    """
    cutoff = _finite_number(cutoff, 'cutoff')
    latest = {}
    rows_used = 0
    for row in records:
        if row['observed'] <= cutoff:
            rows_used += 1
            ticket = row['ticket']
            key = (row['observed'], row['revision'], row['observation_id'])
            if ticket not in latest or key > latest[ticket][0]:
                latest[ticket] = (key, row)

    cohort = []
    for ticket in sorted(latest):
        _, row = latest[ticket]
        if row['event'] not in VALID_EVENTS:
            raise ValueError(f"未知事件类型：{row['event']}")
        if row['opened'] > cutoff:
            raise ValueError(f'第 {ticket} 号 ticket 的 opened 晚于截止时点')
        follow_up = row['observed'] - row['opened']
        if follow_up < 0:
            raise ValueError(f'第 {ticket} 号 ticket 的 observed 早于 opened')
        status = (STATUS_COMPLETE if row['event'] == COMPLETE
                  else STATUS_CANCEL if row['event'] == CANCEL
                  else STATUS_CENSORED)
        cohort.append({
            'ticket': ticket,
            'channel': row['channel'],
            'product': row['product'],
            'opened': row['opened'],
            'observed': row['observed'],
            'revision': row['revision'],
            'event': row['event'],
            'status': status,
            'time': follow_up,
        })
    return cohort, {'rows_used': rows_used, 'tickets_seen': len(latest)}


def queue_version(cutoff, cohort):
    """Stable identifier of the reconstructed queue."""
    canonical = [
        {key: ticket[key] for key in
         ('ticket', 'channel', 'product', 'opened', 'observed', 'revision', 'event', 'time')}
        for ticket in cohort
    ]
    payload = json.dumps({'algorithm': ALGORITHM, 'cutoff': cutoff, 'cohort': canonical},
                         ensure_ascii=False, sort_keys=True, allow_nan=False)
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]
    return f'{ALGORITHM}@{digest}'


def _event_counts(tickets):
    completed = sum(ticket['status'] == STATUS_COMPLETE for ticket in tickets)
    cancelled = sum(ticket['status'] == STATUS_CANCEL for ticket in tickets)
    return {'complete': completed, 'cancel': cancelled,
            'waiting': len(tickets) - completed - cancelled}


def _by_product(tickets):
    groups = {}
    for product in sorted({ticket['product'] for ticket in tickets}):
        members = [ticket for ticket in tickets if ticket['product'] == product]
        groups[product] = {'enrolled': len(members),
                           'event_counts': _event_counts(members)}
    return groups


def _aalen_johansen_steps(tickets):
    """Cumulative incidence at every unique event time.

    Censoring times do not create steps, but tickets censored exactly at an
    event time remain in that time's risk set.
    """
    event_times = sorted({ticket['time'] for ticket in tickets
                          if ticket['status'] != STATUS_CENSORED})
    steps = []
    completion = cancellation = 0.0
    for time in event_times:
        at_risk = sum(ticket['time'] >= time for ticket in tickets)
        complete_events = sum(ticket['time'] == time and ticket['status'] == STATUS_COMPLETE
                              for ticket in tickets)
        cancel_events = sum(ticket['time'] == time and ticket['status'] == STATUS_CANCEL
                            for ticket in tickets)
        if at_risk:
            event_free = 1.0 - completion - cancellation
            completion += (complete_events / at_risk) * event_free
            cancellation += (cancel_events / at_risk) * event_free
        steps.append({
            'time': time,
            'at_risk': at_risk,
            'complete_events': complete_events,
            'cancel_events': cancel_events,
            'completion': completion,
            'cancellation': cancellation,
            'waiting': max(0.0, 1.0 - completion - cancellation),
        })
    return steps


def _horizon_state(tickets, horizon):
    completion = cancellation = 0.0
    for step in _aalen_johansen_steps(tickets):
        if step['time'] <= horizon:
            completion = step['completion']
            cancellation = step['cancellation']
        else:
            break
    waiting = max(0.0, 1.0 - completion - cancellation) if tickets else 0.0
    return {
        'time': horizon,
        'at_risk': sum(ticket['time'] >= horizon for ticket in tickets),
        'completed': sum(ticket['status'] == STATUS_COMPLETE and ticket['time'] <= horizon
                         for ticket in tickets),
        'cancelled': sum(ticket['status'] == STATUS_CANCEL and ticket['time'] <= horizon
                         for ticket in tickets),
        'completion': completion,
        'cancellation': cancellation,
        'waiting': waiting,
    }


def _curve(tickets, horizon):
    steps = _aalen_johansen_steps(tickets)
    rows = []
    if not steps or steps[0]['time'] > 0:
        rows.append({
            'time': 0,
            'at_risk': len(tickets),
            'complete_events': 0,
            'cancel_events': 0,
            'completion': 0.0,
            'cancellation': 0.0,
            'waiting': 1.0 if tickets else 0.0,
        })
    rows.extend(steps)
    if not any(row['time'] == horizon for row in rows):
        state = _horizon_state(tickets, horizon)
        rows.append({
            'time': horizon,
            'at_risk': state['at_risk'],
            'complete_events': sum(ticket['time'] == horizon
                                   and ticket['status'] == STATUS_COMPLETE
                                   for ticket in tickets),
            'cancel_events': sum(ticket['time'] == horizon
                                 and ticket['status'] == STATUS_CANCEL
                                 for ticket in tickets),
            'completion': state['completion'],
            'cancellation': state['cancellation'],
            'waiting': state['waiting'],
        })
        rows.sort(key=lambda row: row['time'])
    return rows


def _triplet(tickets, horizon):
    state = _horizon_state(tickets, horizon)
    return (state['completion'], state['cancellation'], state['waiting'])


def _cell_estimate(tickets, channel, product, horizon):
    members = [ticket for ticket in tickets
               if ticket['channel'] == channel and ticket['product'] == product]
    if not members:
        return None
    return _triplet(members, horizon)


def _weighted(tickets, channel, products, weights, horizon):
    """Direct-standardized estimate for one channel, or None without support."""
    values = {}
    for product in products:
        value = _cell_estimate(tickets, channel, product, horizon)
        if value is None:
            return None
        values[product] = value
    return tuple(sum(weights[product] * values[product][index] for product in products)
                 for index in range(3))


def _standard_weights(tickets, products):
    members = [ticket for ticket in tickets if ticket['product'] in products]
    if not members:
        return {}
    counts = Counter(ticket['product'] for ticket in members)
    return {product: counts[product] / len(members) for product in products}


def _percentile(values, quantile):
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _intervals(samples, confidence):
    usable = [sample for sample in samples if sample is not None]
    if not usable:
        return None
    low = (1.0 - confidence) / 2.0
    high = 1.0 - low
    return {
        metric: [_percentile([sample[index] for sample in usable], low),
                 _percentile([sample[index] for sample in usable], high)]
        for index, metric in enumerate(METRICS)
    }


def _resample(members, rng):
    size = len(members)
    if not size:
        return []
    return [members[rng.randrange(size)] for _ in range(size)]


def _pair_difference(tickets, reference, group, products, weights, horizon):
    reference_value = _weighted(tickets, reference, products, weights, horizon)
    group_value = _weighted(tickets, group, products, weights, horizon)
    if reference_value is None or group_value is None:
        return None
    return tuple(reference_value[index] - group_value[index] for index in range(3))


def _product_support(tickets, channels):
    by_channel = {
        channel: {ticket['product'] for ticket in tickets if ticket['channel'] == channel}
        for channel in channels
    }
    if channels:
        common = set.intersection(*(by_channel[channel] for channel in channels))
    else:
        common = set()
    union = set().union(*(by_channel[channel] for channel in channels)) if channels else set()
    return by_channel, common, union


def analyze(records, cutoff, horizon=24.0, replicates=200, seed=20240101,
            confidence=0.95):
    """Analyze competing risks on the queue known at ``cutoff``.

    ``horizon`` is measured from ``opened`` in the same unit as the time
    fields (hours).  Bootstrap intervals resample tickets, never rows, so
    repeated revisions of one ticket cannot be mistaken for independent
    observations.
    """
    cutoff = _finite_number(cutoff, 'cutoff')
    horizon = _finite_number(horizon, 'horizon')
    if horizon < 0:
        raise ValueError('horizon 不能为负数')
    if replicates < 0:
        raise ValueError('replicates 不能为负数')
    if not 0 < confidence < 1:
        raise ValueError('confidence 必须在 0 和 1 之间')

    cohort, meta = build_cohort(records, cutoff)
    version = queue_version(cutoff, cohort)
    channels = sorted({ticket['channel'] for ticket in cohort})

    overview = {
        'enrolled': len(cohort),
        'event_counts': _event_counts(cohort),
        'by_product': _by_product(cohort),
        'by_channel': {channel: _event_counts(
            [ticket for ticket in cohort if ticket['channel'] == channel])
            for channel in channels},
    }

    # ---- point estimates -------------------------------------------------
    channel_results = []
    for channel in channels:
        members = [ticket for ticket in cohort if ticket['channel'] == channel]
        channel_results.append({
            'group': channel,
            'enrolled': len(members),
            'event_counts': _event_counts(members),
            'by_product': _by_product(members),
            'curve': _curve(members, horizon),
            'at_horizon': _horizon_state(members, horizon),
            'intervals': {metric: None for metric in METRICS},
        })

    by_channel_products, common_products, union_products = _product_support(cohort, channels)
    common_products = sorted(common_products)
    weights = _standard_weights(cohort, common_products)

    non_common = []
    for product in sorted(union_products - set(common_products)):
        present = [channel for channel in channels
                   if product in by_channel_products[channel]]
        missing = [channel for channel in channels
                   if product not in by_channel_products[channel]]
        non_common.append({'product': product, 'present_in': present, 'missing_from': missing})

    standardized_channels = []
    for channel in channels:
        available = bool(common_products) and set(common_products) <= by_channel_products[channel]
        value = _weighted(cohort, channel, common_products, weights, horizon) \
            if common_products else None
        standardized_channels.append({
            'group': channel,
            'complete_support': bool(available),
            'standardized_at_horizon': (
                {'time': horizon, **dict(zip(METRICS, value))} if value is not None else None
            ),
            'intervals': {metric: None for metric in METRICS},
            'missing_common_products': [product for product in common_products
                                        if product not in by_channel_products[channel]],
            'outside_common_products': [product for product in sorted(union_products)
                                        if product in by_channel_products[channel]
                                        and product not in common_products],
        })

    pairs = []
    for left in range(len(channels)):
        for right in range(left + 1, len(channels)):
            reference, group = channels[left], channels[right]
            if common_products:
                pair_products = common_products
                pair_weights = weights
                scope = 'all-channel-common'
            else:
                shared = sorted(by_channel_products[reference]
                                & by_channel_products[group])
                pair_products = shared
                pair_weights = _standard_weights(
                    [ticket for ticket in cohort
                     if ticket['channel'] in (reference, group)], shared) if shared else {}
                scope = 'pair-common'
            pair_incomparable = []
            pair_union = by_channel_products[reference] | by_channel_products[group]
            for product in sorted(pair_union - set(pair_products)):
                pair_incomparable.append({
                    'product': product,
                    'present_in': [channel for channel in (reference, group)
                                   if product in by_channel_products[channel]],
                    'missing_from': [channel for channel in (reference, group)
                                     if product not in by_channel_products[channel]],
                })
            comparable = bool(pair_products)
            differences = _pair_difference(
                cohort, reference, group, pair_products, pair_weights, horizon
            ) if comparable else None
            pairs.append({
                'reference': reference,
                'group': group,
                'standardization_scope': scope,
                'common_products': list(pair_products),
                'standard_weights': pair_weights,
                'comparable': comparable,
                'incomparable_products': pair_incomparable,
                'differences': ({metric: difference for metric, difference
                                 in zip(METRICS, differences)} if differences is not None else None),
                'intervals': {metric: None for metric in METRICS},
            })

    # ---- ticket-cluster bootstrap ---------------------------------------
    if replicates:
        rng = random.Random(seed)
        groups = {channel: [ticket for ticket in cohort if ticket['channel'] == channel]
                  for channel in channels}
        overall_samples = []
        crude_samples = {channel: [] for channel in channels}
        standardized_samples = {channel: [] for channel in channels}
        pair_samples = {(pair['reference'], pair['group']): [] for pair in pairs}

        for _ in range(replicates):
            overall_samples.append(_triplet(_resample(cohort, rng), horizon))
            resampled = {channel: _resample(groups[channel], rng) for channel in channels}
            combined = [ticket for channel in channels for ticket in resampled[channel]]
            for channel in channels:
                crude_samples[channel].append(_triplet(resampled[channel], horizon))
                if common_products:
                    standardized_samples[channel].append(
                        _weighted(combined, channel, common_products, weights, horizon))
                else:
                    standardized_samples[channel].append(None)
            for pair in pairs:
                key = (pair['reference'], pair['group'])
                if pair['comparable']:
                    pair_samples[key].append(_pair_difference(
                        combined, pair['reference'], pair['group'],
                        pair['common_products'], pair['standard_weights'], horizon))
                else:
                    pair_samples[key].append(None)

        overall_intervals = _intervals(overall_samples, confidence)
        for channel_result in channel_results:
            channel_result['intervals'] = _intervals(crude_samples[channel_result['group']], confidence)
        for channel_result in standardized_channels:
            channel_result['intervals'] = _intervals(
                standardized_samples[channel_result['group']], confidence)
        for pair in pairs:
            pair['intervals'] = _intervals(
                pair_samples[(pair['reference'], pair['group'])], confidence)
    else:
        overall_intervals = None

    return {
        'product': PRODUCT,
        'analysis_version': 1,
        'unit': UNIT,
        'cutoff': cutoff,
        'horizon': horizon,
        'primary_outcome': {
            'measure': 'cumulative incidence of completion',
            'event': COMPLETE,
            'competing_event': CANCEL,
            'horizon': horizon,
        },
        'queue_version': version,
        'queue': {
            'version': version,
            'algorithm': ALGORITHM,
            'cutoff': cutoff,
            'definition': DEFINITION,
            'tickets': [
                {key: ticket[key] for key in
                 ('ticket', 'channel', 'product', 'opened', 'observed',
                  'revision', 'event', 'time')}
                for ticket in cohort
            ],
        },
        'input': {
            'rows_used': meta['rows_used'],
            'tickets_seen': meta['tickets_seen'],
        },
        'bootstrap': {
            'method': 'ticket-cluster percentile bootstrap',
            'stratified_by_channel': True,
            'replicates': replicates,
            'seed': seed,
            'confidence': confidence,
        },
        'cohort': overview,
        'overall': {
            'enrolled': len(cohort),
            'event_counts': _event_counts(cohort),
            'curve': _curve(cohort, horizon),
            'at_horizon': _horizon_state(cohort, horizon),
            'intervals': overall_intervals,
        },
        'channels': channel_results,
        'standardized_comparison': {
            'method': 'direct standardization to a pooled common-product structure',
            'common_products': common_products,
            'standard_weights': weights,
            'non_common_products': non_common,
            'channels': standardized_channels,
            'pairwise': pairs,
        },
    }
