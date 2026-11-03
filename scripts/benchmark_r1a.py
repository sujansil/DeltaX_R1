#!/usr/bin/env python3
"""Synchronized R1A HTTP benchmark; safe only against a rehearsal deployment.

Input is a JSON list of distinct-team requests:
[{"token":"...", "option_id":123}, ...]
Tokens and answer values are never printed.
"""
import argparse
import asyncio
import json
import math
from pathlib import Path
from time import perf_counter

import httpx


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    index = (len(ordered) - 1) * fraction
    low, high = math.floor(index), math.ceil(index)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def summary(values):
    return {name: round(value, 3) for name, value in {
        "p50_ms": percentile(values, .50), "p95_ms": percentile(values, .95),
        "p99_ms": percentile(values, .99), "max_ms": max(values),
    }.items()} if values else None


def receipt_latency(header):
    for item in header.split(","):
        name, *parameters = item.strip().split(";")
        if name == "receipt_to_response":
            for parameter in parameters:
                if parameter.startswith("dur="):
                    return float(parameter[4:])
    return None


async def run(base_url, requests, timeout):
    gate = asyncio.Event()
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout,
                                 limits=httpx.Limits(max_connections=len(requests))) as client:
        async def one(item):
            await gate.wait()
            started = perf_counter()
            try:
                response = await client.post("/api/r1a/submit",
                    headers={"Authorization": f"Bearer {item['token']}"},
                    json={"option_id": item["option_id"]})
                rtt = (perf_counter() - started) * 1000
                return response.status_code, rtt, receipt_latency(response.headers.get("server-timing", ""))
            except Exception:
                return 0, (perf_counter() - started) * 1000, None
        tasks = [asyncio.create_task(one(item)) for item in requests]
        await asyncio.sleep(.1)
        gate.set()
        return await asyncio.gather(*tasks)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--requests", required=True, type=Path)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    items = json.loads(args.requests.read_text())
    if len(items) < args.count:
        raise SystemExit(f"need {args.count} distinct-team request records, found {len(items)}")
    results = asyncio.run(run(args.base_url.rstrip("/"), items[:args.count], args.timeout))
    client = [item[1] for item in results]
    server = [item[2] for item in results if item[2] is not None]
    statuses = {}
    for status, _, _ in results:
        statuses[str(status)] = statuses.get(str(status), 0) + 1
    report = {"requests": len(results), "statuses": statuses,
              "errors": sum(status == 0 or status >= 500 for status, _, _ in results),
              "client_round_trip": summary(client),
              "server_receipt_to_response": summary(server),
              "server_timing_samples": len(server)}
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n")


if __name__ == "__main__":
    main()
