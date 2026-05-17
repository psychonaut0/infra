#!/usr/bin/env python3
"""Pair RAW (DNG) + JPG shots in Immich and stack them via the API.

Pixel cameras emit two files per shot when RAW is enabled:
  * <ts>.RAW-02.ORIGINAL.dng (or older: <ts>.dng)
  * <ts>.RAW-01[.MP].COVER.jpg (or older: <ts>[.MP].jpg)

Both end up in Immich as separate assets. Immich's perceptual-hash duplicate
detector can't pair them because the rendered images differ. This script
groups them by their shared timestamp prefix and creates Immich stacks with
the JPG as the primary (cover) asset.

Usage:
  immich-raw-stack.py --dry-run       # default: report only
  immich-raw-stack.py --apply         # create stacks
  immich-raw-stack.py --csv pairs.csv # write inventory
"""
import argparse
import csv
import json
import os
import re
import sys
import urllib.request
from collections import defaultdict

API = os.environ.get("IMMICH_API", "http://127.0.0.1:2283/api")
KEY_PATH = os.environ.get("IMMICH_API_KEY_FILE", "/root/.config/immich-tools/api-key")

EXT_RE = re.compile(r"\.(dng|jpg|jpeg|heic|png)$", re.I)
SUFFIX_RE = re.compile(r"\.RAW-\d+(\.MP)?(\.COVER|\.ORIGINAL)$", re.I)
MP_RE = re.compile(r"\.MP$")


def shotkey(filename: str) -> str:
    base = EXT_RE.sub("", filename)
    base = SUFFIX_RE.sub("", base)
    base = MP_RE.sub("", base)
    return base


def load_key() -> str:
    with open(KEY_PATH) as f:
        return f.read().strip()


def api_request(path: str, key: str, method: str = "GET", body=None):
    url = f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("x-api-key", key)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def fetch_all_images(key: str):
    page = 1
    while True:
        body = {"type": "IMAGE", "size": 1000, "page": page, "withStacked": True}
        resp = api_request("/search/metadata", key, "POST", body)
        items = resp["assets"]["items"]
        if not items:
            return
        for a in items:
            yield a
        nxt = resp["assets"].get("nextPage")
        if not nxt:
            return
        page = int(nxt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually create stacks")
    ap.add_argument("--dry-run", action="store_true", help="report only (default)")
    ap.add_argument("--csv", help="write pair inventory CSV to this path")
    ap.add_argument("--limit", type=int, help="stop after N pairs (debug)")
    args = ap.parse_args()
    apply = args.apply and not args.dry_run

    key = load_key()
    print(f"[info] fetching all images from {API}...", file=sys.stderr)
    by_shot = defaultdict(list)
    total = 0
    for a in fetch_all_images(key):
        total += 1
        sk = shotkey(a["originalFileName"])
        by_shot[(a["ownerId"], sk)].append(a)
    print(f"[info] {total} images, {len(by_shot)} shot keys", file=sys.stderr)

    pairs = []
    for (_owner, sk), members in by_shot.items():
        dngs = [m for m in members if m["originalFileName"].lower().endswith(".dng")]
        jpgs = [m for m in members if re.search(r"\.(jpg|jpeg)$", m["originalFileName"], re.I)]
        if dngs and jpgs:
            pairs.append((sk, jpgs, dngs))

    print(f"[info] {len(pairs)} groups have both DNG and JPG", file=sys.stderr)

    already_stacked = sum(1 for _, jpgs, dngs in pairs
                          for a in jpgs + dngs if a.get("stack") or a.get("stackId"))
    if already_stacked:
        print(f"[info] {already_stacked} assets already in a stack", file=sys.stderr)

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["shot_key", "jpg_file", "jpg_id", "jpg_path",
                        "dng_file", "dng_id", "dng_path", "captured_at"])
            for sk, jpgs, dngs in pairs:
                for j in jpgs:
                    for d in dngs:
                        w.writerow([sk, j["originalFileName"], j["id"], j["originalPath"],
                                    d["originalFileName"], d["id"], d["originalPath"],
                                    j.get("fileCreatedAt", "")])
        print(f"[info] wrote {args.csv}", file=sys.stderr)

    if not apply:
        print("[info] dry run: no stacks created. Re-run with --apply to create them.", file=sys.stderr)
        return

    created = 0
    skipped = 0
    failed = 0
    for i, (sk, jpgs, dngs) in enumerate(pairs):
        if args.limit and created >= args.limit:
            break
        # Skip if any asset is already in a stack
        if any(a.get("stack") for a in jpgs + dngs):
            skipped += 1
            continue
        # JPG primary; if multiple JPGs (e.g. .MP.jpg + plain), prefer the larger one
        primary = max(jpgs, key=lambda a: a.get("exifInfo", {}).get("fileSizeInByte", 0)
                                       or len(a["originalFileName"]))
        children = [a for a in (jpgs + dngs) if a["id"] != primary["id"]]
        asset_ids = [primary["id"]] + [a["id"] for a in children]
        try:
            api_request("/stacks", key, "POST", {"assetIds": asset_ids})
            created += 1
            if created % 25 == 0:
                print(f"[info] created {created} stacks...", file=sys.stderr)
        except Exception as e:
            failed += 1
            print(f"[warn] stack failed for {sk}: {e}", file=sys.stderr)

    print(f"[done] created={created} skipped={skipped} failed={failed}", file=sys.stderr)


if __name__ == "__main__":
    main()
