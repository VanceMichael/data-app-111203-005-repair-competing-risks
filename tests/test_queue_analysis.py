import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from analysis_app.queue import analyze, build_cohort, queue_version
from analysis_app.summary import summarize


def make_row(ticket, channel, product, opened, observed, event, revision,
             duration=None, observation_id=None):
    return {
        'observation_id': observation_id or f'{ticket}-r{revision}',
        'ticket': ticket,
        'channel': channel,
        'duration': float(observed - opened if duration is None else duration),
        'event': event,
        'opened': opened,
        'observed': observed,
        'product': product,
        'revision': revision,
    }


def horizon_of(result, group=None):
    if group is None:
        return result['overall']['at_horizon']
    for channel in result['channels']:
        if channel['group'] == group:
            return channel['at_horizon']
    raise KeyError(group)


class CohortSelectionTest(unittest.TestCase):
    def test_latest_known_revision_selected(self):
        rows = [
            make_row('T1', 'old', 'P0', 0, 1, 'waiting', 0),
            make_row('T1', 'old', 'P0', 0, 5, 'complete', 1),
            make_row('T1', 'old', 'P0', 0, 9, 'cancel', 2),
        ]
        cohort, meta = build_cohort(rows, cutoff=6)
        self.assertEqual(meta['rows_used'], 2)
        self.assertEqual(len(cohort), 1)
        self.assertEqual(cohort[0]['revision'], 1)
        self.assertEqual(cohort[0]['event'], 'complete')
        self.assertEqual(cohort[0]['time'], 5)

    def test_opened_after_cutoff_not_enrolled(self):
        rows = [make_row('T1', 'old', 'P0', 10, 11, 'waiting', 0)]
        cohort, _ = build_cohort(rows, cutoff=5)
        self.assertEqual(cohort, [])

    def test_unknown_event_rejected(self):
        rows = [make_row('T1', 'old', 'P0', 0, 1, 'lost', 0)]
        with self.assertRaisesRegex(ValueError, '未知事件'):
            build_cohort(rows, cutoff=1)

    def test_duration_field_does_not_drive_follow_up(self):
        base = [make_row('T1', 'old', 'P0', 2, 7, 'complete', 0)]
        odd = [make_row('T1', 'old', 'P0', 2, 7, 'complete', 0, duration=999.0)]
        self.assertEqual(analyze(base, 10, horizon=10, replicates=0),
                         analyze(odd, 10, horizon=10, replicates=0))

    def test_queue_version_tracks_cutoff(self):
        rows = [
            make_row('T1', 'old', 'P0', 0, 1, 'waiting', 0),
            make_row('T1', 'old', 'P0', 0, 5, 'complete', 1),
        ]
        early, _ = build_cohort(rows, 1)
        late, _ = build_cohort(rows, 6)
        self.assertNotEqual(queue_version(1, early), queue_version(6, late))
        self.assertEqual(queue_version(1, early), queue_version(1, early))


class CompetingRisksTest(unittest.TestCase):
    def test_no_completion_events(self):
        rows = [
            make_row('T1', 'old', 'P0', 0, 4, 'cancel', 0),
            make_row('T2', 'old', 'P0', 0, 2, 'waiting', 0),
            make_row('T3', 'old', 'P0', 0, 6, 'waiting', 0),
        ]
        result = analyze(rows, cutoff=6, horizon=10, replicates=0)
        state = horizon_of(result)
        self.assertEqual(state['completion'], 0.0)
        self.assertEqual(state['cancellation'], 0.5)
        self.assertEqual(state['waiting'], 0.5)
        self.assertEqual(result['overall']['event_counts'],
                         {'complete': 0, 'cancel': 1, 'waiting': 2})

    def test_tied_outcomes_share_risk_set(self):
        rows = [
            make_row('T1', 'old', 'P0', 0, 5, 'complete', 0),
            make_row('T2', 'old', 'P0', 0, 5, 'complete', 0),
            make_row('T3', 'old', 'P0', 0, 5, 'complete', 0),
            make_row('T4', 'old', 'P0', 0, 5, 'cancel', 0),
            make_row('T5', 'old', 'P0', 0, 5, 'waiting', 0),
            make_row('T6', 'old', 'P0', 0, 5, 'waiting', 0),
        ]
        result = analyze(rows, cutoff=5, horizon=10, replicates=0)
        state = horizon_of(result)
        self.assertAlmostEqual(state['completion'], 3 / 6)
        self.assertAlmostEqual(state['cancellation'], 1 / 6)
        self.assertAlmostEqual(state['waiting'], 2 / 6)
        step = [row for row in result['overall']['curve'] if row['time'] == 5][0]
        self.assertEqual(step['at_risk'], 6)
        self.assertEqual(step['complete_events'], 3)
        self.assertEqual(step['cancel_events'], 1)

    def test_all_censored(self):
        rows = [
            make_row('T1', 'old', 'P0', 0, 3, 'waiting', 0),
            make_row('T2', 'old', 'P0', 0, 7, 'waiting', 0),
        ]
        result = analyze(rows, cutoff=7, horizon=5, replicates=0)
        state = horizon_of(result)
        self.assertEqual(state['completion'], 0.0)
        self.assertEqual(state['cancellation'], 0.0)
        self.assertEqual(state['waiting'], 1.0)
        horizon_row = [row for row in result['overall']['curve'] if row['time'] == 5][0]
        self.assertEqual(horizon_row['at_risk'], 1)
        self.assertEqual(horizon_row['waiting'], 1.0)

    def test_cancel_is_competing_not_censoring(self):
        rows = [
            make_row('T1', 'old', 'P0', 0, 5, 'complete', 0),
            make_row('T2', 'old', 'P0', 0, 5, 'cancel', 0),
            make_row('T3', 'old', 'P0', 0, 10, 'waiting', 0),
        ]
        result = analyze(rows, cutoff=10, horizon=10, replicates=0)
        state = horizon_of(result)
        self.assertAlmostEqual(state['completion'], 1 / 3)
        self.assertAlmostEqual(state['cancellation'], 1 / 3)
        self.assertAlmostEqual(state['waiting'], 1 / 3)

    def test_empty_cohort(self):
        rows = [make_row('T1', 'old', 'P0', 5, 6, 'waiting', 0)]
        result = analyze(rows, cutoff=1, horizon=5, replicates=0)
        self.assertEqual(result['cohort']['enrolled'], 0)
        self.assertEqual(result['overall']['event_counts'],
                         {'complete': 0, 'cancel': 0, 'waiting': 0})
        self.assertEqual(result['channels'], [])


class StandardizationTest(unittest.TestCase):
    def test_new_channel_missing_product_is_not_filled(self):
        rows = [
            make_row('O1', 'old', 'P0', 0, 5, 'complete', 0),
            make_row('O2', 'old', 'P1', 0, 5, 'complete', 0),
            make_row('N1', 'new', 'P0', 0, 5, 'cancel', 0),
        ]
        result = analyze(rows, cutoff=5, horizon=10, replicates=0)
        comparison = result['standardized_comparison']
        self.assertEqual(comparison['common_products'], ['P0'])
        self.assertEqual(comparison['standard_weights'], {'P0': 1.0})
        self.assertEqual(comparison['non_common_products'],
                         [{'product': 'P1', 'present_in': ['old'], 'missing_from': ['new']}])
        channels = {entry['group']: entry for entry in comparison['channels']}
        self.assertEqual(channels['new']['outside_common_products'], [])
        self.assertEqual(channels['old']['outside_common_products'], ['P1'])
        # The new channel is standardized on the common product only; its
        # missing P1 stratum is reported, never extrapolated.
        self.assertEqual(channels['new']['standardized_at_horizon']['completion'], 0.0)
        self.assertEqual(channels['new']['standardized_at_horizon']['cancellation'], 1.0)
        pair = comparison['pairwise'][0]
        self.assertTrue(pair['comparable'])
        self.assertEqual((pair['reference'], pair['group']), ('new', 'old'))
        self.assertEqual(pair['differences']['completion'], -1.0)
        self.assertEqual(pair['incomparable_products'],
                         [{'product': 'P1', 'present_in': ['old'], 'missing_from': ['new']}])

    def test_no_common_support_is_reported_not_extrapolated(self):
        rows = [
            make_row('A1', 'alpha', 'P0', 0, 5, 'complete', 0),
            make_row('B1', 'beta', 'P1', 0, 5, 'cancel', 0),
        ]
        result = analyze(rows, cutoff=5, horizon=10, replicates=0)
        comparison = result['standardized_comparison']
        self.assertEqual(comparison['common_products'], [])
        for entry in comparison['channels']:
            self.assertFalse(entry['complete_support'])
            self.assertIsNone(entry['standardized_at_horizon'])
        pair = comparison['pairwise'][0]
        self.assertFalse(pair['comparable'])
        self.assertIsNone(pair['differences'])
        self.assertEqual({item['product'] for item in pair['incomparable_products']},
                         {'P0', 'P1'})

    def test_standard_weights_follow_pooled_common_structure(self):
        rows = [
            make_row('O1', 'old', 'P0', 0, 5, 'complete', 0),
            make_row('O2', 'old', 'P0', 0, 5, 'waiting', 0),
            make_row('O3', 'old', 'P1', 0, 5, 'complete', 0),
            make_row('N1', 'new', 'P0', 0, 5, 'cancel', 0),
            make_row('N2', 'new', 'P1', 0, 5, 'waiting', 0),
        ]
        result = analyze(rows, cutoff=5, horizon=10, replicates=0)
        weights = result['standardized_comparison']['standard_weights']
        self.assertAlmostEqual(weights['P0'], 3 / 5)
        self.assertAlmostEqual(weights['P1'], 2 / 5)


class FutureIsolationTest(unittest.TestCase):
    def base_rows(self):
        return [
            make_row('T1', 'old', 'P0', 0, 1, 'waiting', 0),
            make_row('T1', 'old', 'P0', 0, 5, 'complete', 1),
            make_row('T2', 'old', 'P1', 0, 2, 'waiting', 0),
            make_row('T2', 'old', 'P1', 0, 8, 'waiting', 1),
        ]

    def test_revisions_after_cutoff_ignored(self):
        result = analyze(self.base_rows(), cutoff=6, horizon=10, replicates=0)
        self.assertEqual(result['cohort']['event_counts'],
                         {'complete': 1, 'cancel': 0, 'waiting': 1})
        selected = {ticket['ticket']: ticket for ticket in result['queue']['tickets']}
        self.assertEqual(selected['T1']['revision'], 1)
        self.assertEqual(selected['T2']['revision'], 0)

    def test_historical_cutoff_rerun_unaffected_by_future_outcomes(self):
        base = analyze(self.base_rows(), cutoff=6, horizon=10, replicates=20, seed=7)
        future_rows = self.base_rows() + [
            make_row('T1', 'old', 'P0', 0, 7, 'cancel', 2),
            make_row('T2', 'old', 'P1', 0, 9, 'complete', 2),
            make_row('T3', 'old', 'P0', 7, 8, 'complete', 0),
        ]
        rerun = analyze(future_rows, cutoff=6, horizon=10, replicates=20, seed=7)
        self.assertEqual(base, rerun)
        self.assertEqual(base['queue_version'], rerun['queue_version'])

    def test_later_cutoff_sees_more(self):
        early = analyze(self.base_rows(), cutoff=6, horizon=10, replicates=0)
        late = analyze(self.base_rows(), cutoff=8, horizon=10, replicates=0)
        self.assertNotEqual(early['queue_version'], late['queue_version'])
        self.assertEqual(late['cohort']['event_counts']['waiting'], 1)


class IntervalTest(unittest.TestCase):
    def test_ticket_bootstrap_intervals_present(self):
        rows = []
        for index in range(12):
            event = 'complete' if index % 3 == 0 else ('cancel' if index % 3 == 1 else 'waiting')
            rows.append(make_row(f'T{index}', 'old' if index < 6 else 'new',
                                 'P0' if index % 2 else 'P1', 0, 5, event, 0))
        result = analyze(rows, cutoff=5, horizon=10, replicates=60, seed=11)
        intervals = result['overall']['intervals']
        self.assertEqual(set(intervals), {'completion', 'cancellation', 'waiting'})
        for metric, (low, high) in intervals.items():
            self.assertLessEqual(low, high)
        for channel in result['channels']:
            for metric, (low, high) in channel['intervals'].items():
                self.assertLessEqual(low, high)

    def test_bootstrap_is_deterministic(self):
        rows = [make_row(f'T{index}', 'old', 'P0', 0, 5,
                         'complete' if index % 2 else 'waiting', 0)
                for index in range(8)]
        first = analyze(rows, cutoff=5, horizon=10, replicates=30, seed=3)
        second = analyze(rows, cutoff=5, horizon=10, replicates=30, seed=3)
        self.assertEqual(first, second)


class CliTest(unittest.TestCase):
    def test_analysis_command_writes_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'analysis.json'
            run = subprocess.run(
                [sys.executable, '-m', 'analysis_app', 'analysis',
                 '--cutoff', '24', '--horizon', '12',
                 '--bootstrap-replicates', '20', '--output', str(path)],
                text=True, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            written = json.loads(path.read_text())
            printed = json.loads(run.stdout)
            self.assertEqual(written, printed)
            self.assertEqual(written['cutoff'], 24.0)
            self.assertEqual(written['horizon'], 12.0)
            self.assertIn('queue_version', written)
            self.assertIn('cohort', written)
            self.assertIn('channels', written)
            self.assertIn('standardized_comparison', written)

    def test_analysis_requires_cutoff(self):
        run = subprocess.run([sys.executable, '-m', 'analysis_app', 'analysis'],
                             text=True, capture_output=True)
        self.assertNotEqual(run.returncode, 0)

    def test_summary_still_available(self):
        run = subprocess.run([sys.executable, '-m', 'analysis_app', 'summary'],
                             text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result['rows'], 288)
        self.assertIn('groups', result)


if __name__ == '__main__':
    unittest.main()
