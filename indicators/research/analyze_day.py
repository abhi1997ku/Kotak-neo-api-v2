"""Summarize a day JSONL file without modifying the original observations."""
import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def analyze(path):
    path = Path(path)
    latest_rows = {}
    versions = defaultdict(set)
    closed_versions = defaultdict(set)
    contexts = set()
    start = end = None
    samples = missing = malformed = 0
    max_gap = 0.0
    previous_received = None
    for line in path.open(encoding='utf-8'):
        try:
            packet = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        samples += 1
        received = packet['receivedIst']
        start = start or received
        end = received
        stamp = datetime.fromisoformat(received)
        if previous_received:
            max_gap = max(max_gap, (stamp - previous_received).total_seconds())
        previous_received = stamp
        missing += int(not packet.get('tablePresent'))
        rows = packet.get('rows', [])
        context = '|'.join(rows[0]) if rows else 'missing'
        contexts.add(context)
        valid = [r for r in rows if len(r) == 18]
        if not valid:
            continue
        newest = max(float(r[7].replace(',', '')) for r in valid)
        for r in valid:
            epoch = float(r[7].replace(',', ''))
            key = (context, epoch)
            latest_rows[key] = r
            # Compare state and marker revisions separately from normal price updates.
            versions[key].add(tuple(r[i] for i in [14, 15, 16]))
            if epoch < newest:
                closed_versions[key].add(tuple(r[i] for i in [1, 2, 3, 4, 9, 13, 14, 15, 16]))
    states = defaultdict(int)
    signal_bars = []
    transitions = []
    candle_gaps = []
    previous = {}
    previous_epoch = {}
    for (context, epoch), row in sorted(latest_rows.items()):
        states[row[16]] += 1
        if any(x not in ['\u2205', '0.00', ''] for x in row[14:16]):
            signal_bars.append({'context': context, 'bar': row[0], 'buy': row[14], 'sell': row[15], 'close': row[4]})
        prev = previous.get(context)
        step = float(row[12].replace(',', ''))
        continuous = prev is not None and epoch - previous_epoch[context] <= step
        if prev and not continuous:
            candle_gaps.append({'context': context, 'from_bar': prev[0], 'to_bar': row[0], 'seconds_between': epoch - previous_epoch[context], 'missing_expected_bars': max(0, round((epoch - previous_epoch[context]) / step) - 1)})
        if prev and prev[16] != row[16]:
            transitions.append({'context': context, 'bar': row[0], 'from': prev[16], 'to': row[16], 'continuous': continuous})
        previous[context] = row
        previous_epoch[context] = epoch
    result = {
        'first_received_ist': start, 'last_received_ist': end, 'samples': samples,
        'distinct_bars': len(latest_rows), 'contexts': sorted(contexts),
        'missing_table_samples': missing, 'malformed_or_partial_lines': malformed,
        'maximum_receive_gap_seconds': max_gap, 'state_counts_latest_observations': dict(states),
        'candle_timestamp_gaps': candle_gaps,
        'signal_bars': signal_bars, 'state_transitions_latest_observations': transitions,
        'bars_with_state_or_signal_revisions': sum(len(v) > 1 for v in versions.values()),
        'bars_with_post_close_proxy_revisions': sum(len(v) > 1 for v in closed_versions.values()),
        'limits': 'Includes initial historical rows; those are not live observations. Forming candles remain provisional. Polling can miss short-lived changes. A newer timestamp in the snapshot is the closure proxy. Gaps and contract/timeframe changes must be considered before fitting rules.'
    }
    target = path.with_name(path.stem + '_analysis.json')
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    temp.replace(target)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('path')
    args = parser.parse_args()
    print(json.dumps(analyze(args.path), indent=2, ensure_ascii=True))
