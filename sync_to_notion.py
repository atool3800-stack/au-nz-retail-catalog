#!/usr/bin/env python3
"""
Annual AU/NZ Retail Catalog 2026 — GitHub -> Notion sync automation.

Reads the latest product & inventory data files from the configured GitHub
repository, cleans them (dedupe by SKU ID, fill missing defaults), and
batch upserts them into the Notion "Annual AU/NZ Retail Catalog 2026"
database. Designed to handle 10,000+ SKUs within 30 minutes using
concurrent, rate-limit-aware writes.

Configuration (environment variables):
  NOTION_TOKEN            Notion integration token (write access to the DB)
  NOTION_DATABASE_ID      Target Notion database ID
  DATA_OWNER              GitHub owner of the source repo (default: atool3800-stack)
  DATA_REPO               GitHub repo holding the data (default: au-nz-retail-catalog)
  DATA_BRANCH             Branch to read (default: main)
  GITHUB_TOKEN            GitHub token (used to resolve the source commit SHA)
  DATA_DIR                Local directory with data files (for local runs)
  MAX_WORKERS             Concurrent Notion write workers (default: 6)
"""

import argparse
import csv
import io
import json
import os
import sys
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

DATA_DIR = os.environ.get("DATA_DIR", "data")
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "6"))
NOTION_VERSION = "2022-06-28"

DEFAULTS = {
    "price": 0,
    "stock_quantity": 0,
    "status": "pending",
    "name": "Unknown Product",
    "supplier": "Unknown",
    "category": "Uncategorised",
}

REQUIRED_FIELDS = {
    "sku_id": "SKU ID",
    "name": "商品名称",
    "category": "品类",
    "price": "价格",
    "stock_quantity": "库存数量",
    "supplier": "供应商",
    "status": "状态",
}


# ---------------------------------------------------------------- helpers
def retry(fn, retries=8, base=1.5):
    last = None
    for i in range(retries):
        try:
            return fn()
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else None
            if code in (429, 500, 502, 503, 504):
                time.sleep(min(base * (2 ** i), 20))
                last = e
                continue
            raise
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            time.sleep(min(base * (2 ** i), 20))
            last = e
    raise last


def gh_api(url, token):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    return retry(lambda: requests.get(url, headers=headers, timeout=60).json())


def notion_headers():
    return {
        "Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------- data load
def fetch_raw_files(owner, repo, branch):
    """Download products_2026.json and inventory_updates_2026.csv from GitHub."""
    base = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}"
    r1 = requests.get(f"{base}/data/products_2026.json", timeout=120)
    r1.raise_for_status()
    products = json.loads(r1.text)
    r2 = requests.get(f"{base}/data/inventory_updates_2026.csv", timeout=120)
    r2.raise_for_status()
    inv = list(csv.DictReader(io.StringIO(r2.text)))
    return products, inv


def load_local_files(data_dir):
    with open(os.path.join(data_dir, "products_2026.json")) as f:
        products = json.load(f)
    with open(os.path.join(data_dir, "inventory_updates_2026.csv")) as f:
        inv = list(csv.DictReader(f))
    return products, inv


def clean_and_merge(products, inventory):
    """Dedupe by SKU ID, merge inventory, fill missing defaults."""
    inv_by_sku = OrderedDict()
    for row in inventory:
        sku = (row.get("sku_id") or "").strip()
        if not sku:
            continue
        try:
            qty = int(float(row["stock_quantity"])) if row.get("stock_quantity") not in (None, "") else None
        except (ValueError, TypeError):
            qty = None
        inv_by_sku[sku] = qty if qty is not None else inv_by_sku.get(sku)

    merged = OrderedDict()
    for p in products:
        sku = (p.get("sku_id") or "").strip()
        if not sku:
            continue
        # later rows win for scalar fields (keeps most recent supplier/status/price)
        rec = merged.get(sku, {})
        for k in ("name", "category", "price", "supplier", "status", "currency"):
            if p.get(k) not in (None, ""):
                rec[k] = p[k]
        merged[sku] = rec

    records = []
    for sku, rec in merged.items():
        rec["sku_id"] = sku
        rec["stock_quantity"] = inv_by_sku.get(sku)
        records.append(rec)

    # fill defaults
    for rec in records:
        for k, default in DEFAULTS.items():
            v = rec.get(k)
            if v is None or (isinstance(v, str) and not v.strip()):
                rec[k] = default
        rec["name"] = str(rec["name"]).strip() or DEFAULTS["name"]
        rec["category"] = str(rec["category"]).strip() or DEFAULTS["category"]
        rec["supplier"] = str(rec["supplier"]).strip() or DEFAULTS["supplier"]
        rec["status"] = str(rec["status"]).strip().lower() or DEFAULTS["status"]
        try:
            rec["price"] = round(float(rec["price"]), 2)
        except (ValueError, TypeError):
            rec["price"] = DEFAULTS["price"]
        try:
            rec["stock_quantity"] = int(float(rec["stock_quantity"]))
        except (ValueError, TypeError):
            rec["stock_quantity"] = DEFAULTS["stock_quantity"]
    return records


# ---------------------------------------------------------------- notion sync
def page_payload(rec, sync_ts):
    return {
        "SKU ID": {"title": [{"text": {"content": rec["sku_id"]}}]},
        "商品名称": {"rich_text": [{"text": {"content": rec["name"]}}]},
        "品类": {"select": {"name": rec["category"]}},
        "价格": {"number": rec["price"]},
        "库存数量": {"number": rec["stock_quantity"]},
        "供应商": {"select": {"name": rec["supplier"]}},
        "状态": {"select": {"name": rec["status"]}},
        "最后同步时间": {"date": {"start": sync_ts}},
    }


def list_existing_pages(db_id):
    """Return {sku_id: page_id} for all pages currently in the DB."""
    url = f"https://api.notion.com/v1/databases/{db_id}/query"
    mapping = {}
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        data = retry(lambda: requests.post(url, headers=notion_headers(), json=body, timeout=60).json())
        for p in data.get("results", []):
            props = p.get("properties", {})
            title = props.get("SKU ID", {}).get("title", [])
            sku = "".join(t.get("plain_text", "") for t in title)
            if sku:
                mapping[sku] = p["id"]
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return mapping


def create_page(db_id, payload):
    url = "https://api.notion.com/v1/pages"
    body = {"parent": {"database_id": db_id}, "properties": payload}
    return retry(lambda: requests.post(url, headers=notion_headers(), json=body, timeout=60).status_code)


def update_page(page_id, payload):
    url = f"https://api.notion.com/v1/pages/{page_id}"
    return retry(lambda: requests.patch(url, headers=notion_headers(), json={"properties": payload}, timeout=60).status_code)


def sync(args):
    owner = os.environ.get("DATA_OWNER", "atool3800-stack")
    repo = os.environ.get("DATA_REPO", "au-nz-retail-catalog")
    branch = os.environ.get("DATA_BRANCH", "main")
    db_id = os.environ["NOTION_DATABASE_ID"]

    # Resolve source commit SHA
    commit_sha = ""
    gh_token = os.environ.get("GITHUB_TOKEN", "")
    try:
        if gh_token:
            info = gh_api(f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}", gh_token)
            commit_sha = info.get("sha", "")
    except Exception as e:
        print(f"[warn] could not resolve commit SHA: {e}", file=sys.stderr)

    # Load data
    if args.local and os.path.isdir(args.local):
        products, inventory = load_local_files(args.local)
    else:
        products, inventory = fetch_raw_files(owner, repo, branch)

    records = clean_and_merge(products, inventory)
    print(f"[sync] raw products={len(products)} raw inventory={len(inventory)} cleaned unique SKUs={len(records)}")

    # Existing pages
    existing = list_existing_pages(db_id)
    print(f"[sync] existing Notion pages={len(existing)}")

    sync_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    to_create = [r for r in records if r["sku_id"] not in existing]
    to_update = [r for r in records if r["sku_id"] in existing]
    print(f"[sync] to_create={len(to_create)} to_update={len(to_update)}")

    success = 0
    failed = []
    lock = None  # simple counter

    def work(rec):
        try:
            if rec["sku_id"] in existing:
                update_page(existing[rec["sku_id"]], page_payload(rec, sync_ts))
            else:
                create_page(db_id, page_payload(rec, sync_ts))
            return True, rec["sku_id"]
        except Exception as e:
            return False, (rec["sku_id"], str(e))

    # create new pages
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(work, r): r["sku_id"] for r in to_create}
        for fut in as_completed(futs):
            ok, info = fut.result()
            if ok:
                success += 1
            else:
                failed.append(info)

    # update existing pages
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(work, r): r["sku_id"] for r in to_update}
        for fut in as_completed(futs):
            ok, info = fut.result()
            if ok:
                success += 1
            else:
                failed.append(info)

    summary = {
        "sync_time": sync_ts,
        "success": success,
        "failed": len(failed),
        "commit_sha": commit_sha,
        "total_skus": len(records),
        "created": len(to_create),
        "updated": len(to_update),
    }
    print(json.dumps(summary, indent=2))
    # write a machine-readable summary for CI
    with open("sync_result.json", "w") as f:
        json.dump(summary, f)
    if failed:
        with open("sync_failed.json", "w") as f:
            json.dump(failed[:200], f)
    return 0 if not failed else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", default=os.environ.get("DATA_DIR", ""), help="local data dir (skip GitHub fetch)")
    args = ap.parse_args()
    sys.exit(sync(args))


if __name__ == "__main__":
    main()
