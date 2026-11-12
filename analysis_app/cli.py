import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from .io import load_records
from .queue import analyze
from .summary import summarize


def save_json(path, result):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.analysis-', dir=destination.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(result, handle, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description='本地观测数据分析')
    commands = parser.add_subparsers(dest='command', required=True)
    summary = commands.add_parser('summary', help='按业务维度核对观测口径')
    summary.add_argument('--input', type=Path, default=Path('data/observations.csv'))
    summary.add_argument('--output', type=Path)

    analysis = commands.add_parser(
        'analysis', help='按截止时点重建队列并做竞争风险分析')
    analysis.add_argument('--input', type=Path, default=Path('data/observations.csv'))
    analysis.add_argument('--output', type=Path)
    analysis.add_argument('--cutoff', type=float, required=True,
                          help='重建队列的截止时点（与 opened/observed 同一时间单位）')
    analysis.add_argument('--horizon', type=float, default=24.0,
                          help='主要结果的指定时长，从 opened 起算')
    analysis.add_argument('--bootstrap-replicates', type=int, default=200,
                          help='按 ticket 抽样的 bootstrap 重复次数')
    analysis.add_argument('--seed', type=int, default=20240101,
                          help='bootstrap 随机种子')
    analysis.add_argument('--confidence', type=float, default=0.95,
                          help='区间置信水平')
    args = parser.parse_args()
    try:
        if args.command == 'summary':
            result = summarize(load_records(args.input))
        else:
            result = analyze(
                load_records(args.input),
                cutoff=args.cutoff,
                horizon=args.horizon,
                replicates=args.bootstrap_replicates,
                seed=args.seed,
                confidence=args.confidence,
            )
        if args.output:
            save_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
