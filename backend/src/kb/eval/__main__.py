"""``python -m kb.eval run --qrels q.jsonl`` / ``python -m kb.eval generate -n 100 --out q.jsonl``."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from kb.db import close_pool, open_pool


def _parser() -> argparse.ArgumentParser:
    from kb.eval.runner import CONFIGS, DEFAULT_CONFIGS

    p = argparse.ArgumentParser(prog="python -m kb.eval", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="compare retrieval configurations on a qrels file")
    run.add_argument("--qrels", required=True, help="JSONL file of queries + relevant ids")
    run.add_argument(
        "--configs",
        default=",".join(DEFAULT_CONFIGS),
        help=f"comma-separated subset of: {', '.join(CONFIGS)}",
    )
    run.add_argument("--k", default="5,10,50", help="recall cut-offs, e.g. 5,10,50")
    run.add_argument("--concurrency", type=int, default=4)
    run.add_argument("--json", dest="json_out", help="also write full results (per query) here")

    gen = sub.add_parser("generate", help="synthesise qrels by asking an LLM about sampled chunks")
    gen.add_argument("-n", type=int, default=100, help="number of chunks to sample")
    gen.add_argument("--out", required=True)
    gen.add_argument("--collection", help="restrict sampling to one collection slug")
    gen.add_argument("--engine", choices=["local", "cloud"], default="local")
    gen.add_argument("--seed", default="kb")
    gen.add_argument("--concurrency", type=int, default=4)
    return p


async def _main(args: argparse.Namespace) -> int:
    await open_pool()
    try:
        if args.cmd == "run":
            from kb.eval.runner import evaluate, format_table, load_qrels

            qrels = load_qrels(args.qrels)
            ks = tuple(int(k) for k in args.k.split(",") if k.strip())
            configs = [c.strip() for c in args.configs.split(",") if c.strip()]
            results = await evaluate(qrels, configs, ks, args.concurrency)
            print(f"{len(qrels)} queries from {args.qrels}\n")
            print(format_table(results))
            if args.json_out:
                payload = json.dumps([asdict(r) for r in results], indent=2, default=str)
                await asyncio.to_thread(Path(args.json_out).write_text, payload, encoding="utf-8")
        else:
            from kb.eval.generate import generate

            written = await generate(
                args.n,
                args.out,
                collection=args.collection,
                engine=args.engine,
                seed=args.seed,
                concurrency=args.concurrency,
            )
            print(f"wrote {written} queries to {args.out}")
    finally:
        from kb.search.pipeline import flush_background

        await flush_background()
        await close_pool()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())
