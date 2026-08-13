# Annual AU/NZ Retail Catalog 2026 — GitHub → Notion Sync Automation

Automated pipeline that aggregates the annual AU/NZ retail product catalog
from a GitHub repository into a Notion database for catalog, inventory audit
and promotion planning.

Team: 8-person e-commerce retail team based in Melbourne (AU) & Auckland (NZ).
Data volume: **12,000+ SKUs** per year, handled **within 30 minutes** via
concurrent, rate-limit-aware batch upserts to the Notion API.

## Repository layout

```
.
├── data/
│   ├── products_2026.json        # 12,600 raw product rows (12,000 unique SKUs; dupes & missing fields)
│   └── inventory_updates_2026.csv# 12,600 raw inventory rows (stock qty, warehouse, last_updated)
├── sync_to_notion.py             # Clean → dedupe → default-fill → batch upsert to Notion
├── generate_data.py              # (Re)generates the source data files
├── requirements.txt              # requests
└── .github/workflows/sync.yml    # Yearly scheduled + on-demand sync via GitHub Actions
```

## Target Notion database

**Annual AU/NZ Retail Catalog 2026** — fields:

| Field        | Type    | Notes                              |
|--------------|---------|------------------------------------|
| SKU ID       | title   | unique product key                 |
| 商品名称     | rich text | product name                      |
| 品类         | select  | category (Electronics, Apparel, …) |
| 价格         | number  | price in AUD/NZD                   |
| 库存数量     | number  | stock quantity                     |
| 供应商       | select  | supplier                           |
| 状态         | select  | active / inactive / discontinued / pending |
| 最后同步时间 | date    | last successful sync timestamp     |

## Data cleaning rules

* **Dedupe** — records are merged by `SKU ID` (later rows win for scalar fields).
* **Missing price** → default `0`.
* **Missing status** → default `pending`.
* **Missing stock** → default `0`.
* **Missing name / supplier / category** → defaults `Unknown Product` / `Unknown` / `Uncategorised`.

## How the sync works

1. GitHub Actions (or manual trigger) checks out the repo.
2. `sync_to_notion.py` pulls `data/products_2026.json` + `data/inventory_updates_2026.csv`
   from the `main` branch and resolves the **source commit SHA**.
3. Data is cleaned/merged as above.
4. Existing Notion pages are indexed by SKU ID (paginated query).
5. New SKUs are **created**, existing SKUs are **updated** — via a thread pool
   with automatic backoff on Notion rate limits (429/5xx). 6 workers sustain
   well over the 3 req/s baseline, so 10,000+ records complete in well under 30 min.
6. A machine-readable `sync_result.json` (sync time / success / failed / commit SHA)
   is uploaded as a workflow artifact and recorded in Notion.

## Setup

1. Create the Notion integration and share the target database with it.
2. Add repository secrets/variables:
   - `NOTION_TOKEN` (secret) — Notion integration token.
   - `NOTION_DATABASE_ID` (variable) — target database UUID.
3. The workflow runs:
   - **Yearly** (Jan 1, 00:00 UTC) — `schedule`
   - **On demand** — `workflow_dispatch`
   - **On data change** — push to `data/*` or `sync_to_notion.py`

## Manual run

Local:
```bash
export NOTION_TOKEN="secret_ntn_..."
export NOTION_DATABASE_ID="<db-uuid>"
export GITHUB_TOKEN="<token>"            # optional, for commit SHA
python sync_to_notion.py --local data/
```

Trigger via GitHub Actions:
```bash
# workflow_dispatch
curl -X POST \
  -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/atool3800-stack/au-nz-retail-catalog/actions/workflows/sync.yml/dispatches \
  -d '{"ref":"main"}'
```

## Observability

Every sync run is recorded in a Notion **Sync Run Logs** page/database with
sync time, success count, failed count and source commit SHA. A **Catalog
Summary 2026** view groups products by 品类 (category) and 供应商 (supplier)
with product counts and total inventory value, shared with the team.
