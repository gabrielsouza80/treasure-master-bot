"""Evaluate human annotations against sequential inspector CSV; no relabeling.

Counts use all certain labels. Angles use complete angle labels and optimal
one-to-one circular matches within the annotation tolerance. Reports include
per-frame false negatives/positives, so aggregate counts cannot hide mistakes.
"""
import argparse
import csv
from functools import lru_cache
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.vision.knife_detector import circular_distance


def match_angles(expected, observed, tolerance):
    """Maximum cardinality, then minimum total distance (small benchmarks)."""
    if len(observed) > 20:
        raise ValueError('Benchmark matching supports at most 20 observations')
    @lru_cache(None)
    def solve(i, used):
        if i == len(expected):
            return ()
        best = solve(i + 1, used)
        for j, angle in enumerate(observed):
            distance = circular_distance(expected[i], angle)
            if used & (1 << j) or distance > tolerance:
                continue
            candidate = ((i, j, distance),) + solve(i + 1, used | (1 << j))
            if (len(candidate), -sum(p[2] for p in candidate)) > (len(best), -sum(p[2] for p in best)):
                best = candidate
        return best
    return solve(0, 0)


def evaluate(annotations, rows, field='stable_angles'):
    tolerance = annotations['angle_tolerance_deg']
    results = []
    errors = []
    tp = fn = fp = 0
    for label in annotations['frames']:
        if label.get('uncertain'):
            continue
        index = label['frame']
        if index not in rows or rows[index]['state'] != 'PLAYING':
            raise ValueError(f'Labeled PLAYING frame {index} missing or not PLAYING')
        row = rows[index]
        if abs(float(row['time']) - label['timestamp']) > .001:
            raise ValueError(f'Timestamp mismatch at {index}; decode sequentially')
        observed = json.loads(row[field])
        delta = len(observed) - label['expected_count']
        result = dict(frame=index, expected=label['expected_count'], observed=len(observed),
                      count_error=delta, categories=label['categories'])
        expected = label.get('angles_deg')
        if expected is not None:
            if len(expected) != label['expected_count']:
                raise ValueError('Partial angle labels must be omitted, not treated as complete')
            pairs = match_angles(expected, observed, tolerance)
            errors.extend(p[2] for p in pairs)
            tp += len(pairs); fn += len(expected) - len(pairs); fp += len(observed) - len(pairs)
            result.update(missed_angles=[a for i,a in enumerate(expected) if i not in {p[0] for p in pairs}],
                          extra_angles=[a for j,a in enumerate(observed) if j not in {p[1] for p in pairs}])
        results.append(result)
    n = len(results)
    if not n:
        raise ValueError('No certain benchmark labels')
    return dict(labeled_frames=n, uncertain_excluded=sum(bool(f.get('uncertain')) for f in annotations['frames']),
                count_exact_accuracy=sum(r['count_error'] == 0 for r in results)/n,
                count_mae=sum(abs(r['count_error']) for r in results)/n,
                undercount_rate=sum(r['count_error'] < 0 for r in results)/n,
                overcount_rate=sum(r['count_error'] > 0 for r in results)/n,
                angle_recall=tp/(tp+fn) if tp+fn else None,
                angle_precision=tp/(tp+fp) if tp+fp else None,
                angle_mae_deg=sum(errors)/len(errors) if errors else None,
                angle_true_positives=tp, angle_false_negatives=fn, angle_false_positives=fp,
                frames=results)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('annotations',type=Path)
    parser.add_argument('csv',type=Path)
    parser.add_argument('--field',choices=('stable_angles','raw_angles'),default='stable_angles')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    with args.csv.open(newline='',encoding='utf-8') as source:
        rows={int(r['frame']):r for r in csv.DictReader(source)}
    result=evaluate(json.loads(args.annotations.read_text(encoding='utf-8')),rows,args.field)
    print(json.dumps({k:v for k,v in result.items() if k!='frames'},indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')


if __name__=='__main__':main()
