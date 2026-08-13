#!/usr/bin/env python3
"""Regenerate the source data files for the Annual AU/NZ Retail Catalog 2026.

Outputs (into ./data unless --outdir is given):
  products_2026.json           ~12,600 raw product rows (12,000 unique SKUs,
                               includes duplicate rows + missing fields)
  inventory_updates_2026.csv   ~12,600 raw inventory rows (includes dupes +
                               missing stock quantities)

Usage:
    python generate_data.py [--outdir data] [--seed 2026]
"""
import argparse
import csv
import json
import os
import random

CATEGORIES = {
    "Electronics": ["4K Smart TV", "Wireless Earbuds", "Bluetooth Speaker", "Laptop", "Smartphone", "Tablet", "Smart Watch", "Gaming Console", "Drone", "Digital Camera", "Router", "Monitor"],
    "Apparel": ["Cotton T-Shirt", "Denim Jeans", "Hoodie", "Running Shoes", "Sneakers", "Jacket", "Polo Shirt", "Dress", "Shorts", "Socks (3-pack)", "Cap", "Scarf"],
    "Home & Kitchen": ["Non-stick Frying Pan", "Coffee Maker", "Blender", "Toaster", "Kettle", "Cookware Set", "Dinnerware Set", "Vacuum Cleaner", "Air Fryer", "Rice Cooker", "Cutlery Set", "Storage Boxes"],
    "Sports & Outdoors": ["Yoga Mat", "Dumbbell Set", "Treadmill", "Tent", "Hiking Backpack", "Bicycle", "Tennis Racket", "Camping Stove", "Fishing Rod", "Surfboard", "Sleeping Bag", "Football"],
    "Beauty & Personal Care": ["Moisturiser", "Shampoo", "Perfume", "Sunscreen SPF50", "Toothbrush (electric)", "Hair Dryer", "Skincare Set", "Makeup Palette", "Razor Kit", "Body Lotion", "Face Mask", "Nail Kit"],
    "Toys & Games": ["Building Blocks Set", "Action Figure", "Board Game", "Remote Control Car", "Plush Toy", "Puzzle", "Dollhouse", "Lego Set", "Water Gun", "Toy Drone", "Card Game", "Science Kit"],
    "Automotive": ["Car Battery", "Engine Oil 5L", "Tyres (set of 4)", "Car Vacuum", "Dash Cam", "Jump Starter", "Floor Mats", "Roof Rack", "Car Charger", "Wiper Blades", "Polishing Kit", "GPS Navigator"],
    "Grocery & Food": ["Organic Coffee Beans", "Olive Oil 1L", "Chocolate Gift Box", "Cereal", "Pasta Pack", "Rice 5kg", "Honey Jar", "Spice Set", "Frozen Seafood", "Protein Bars", "Tea Assortment", "Jam Preserve"],
    "Office & Stationery": ["Office Chair", "Standing Desk", "Printer", "Notebook (A5)", "Stapler", "Whiteboard", "Laminator", "Paper Ream", "Desk Lamp", "Pen Set", "Filing Cabinet", "Desk Organiser"],
    "Health & Wellness": ["Blood Pressure Monitor", "Fitness Tracker", "Thermometer", "Massage Gun", "Electric Blanket", "Air Purifier", "Humidifier", "Vitamins Pack", "First Aid Kit", "Knee Brace", "Pill Organiser", "Digital Scale"],
}

SUPPLIERS = ["Aussie Traders", "Pacific Imports", "Kiwi Supply Co", "Southern Cross Distribution", "Tasman Wholesale", "Harbour City Goods", "Outback Retail Group", "Blue Mountain Supply", "Fern & Co NZ", "Emu Exporters", "Coral Coast Imports", "Rimu Distributors"]
WAREHOUSES = ["Sydney", "Melbourne", "Brisbane", "Perth", "Auckland", "Wellington", "Christchurch", "Adelaide"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="data")
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()
    random.seed(args.seed)
    os.makedirs(args.outdir, exist_ok=True)

    products = []
    counter = 0
    for cat, items in CATEGORIES.items():
        for i in range(1200):
            item = items[i % len(items)]
            sku = f"SKU-{cat[:4].upper()}-{counter+1:05d}"
            products.append({
                "sku_id": sku,
                "name": f"{item} - {cat} Series {(counter % 250) + 1:03d}",
                "category": cat,
                "price": round(random.uniform(4.99, 1499.0), 2),
                "supplier": random.choice(SUPPLIERS),
                "status": random.choices(["active", "inactive", "discontinued", "pending"], weights=[70, 12, 8, 10])[0],
                "currency": random.choice(["AUD", "NZD"]),
            })
            counter += 1

    raw = list(products)
    raw.extend(dict(random.choice(products)) for _ in range(int(len(products) * 0.05)))
    random.shuffle(raw)

    miss_price = set(random.sample(range(len(raw)), int(len(raw) * 0.08)))
    miss_status = set(random.sample(range(len(raw)), int(len(raw) * 0.06)))
    miss_supplier = set(random.sample(range(len(raw)), int(len(raw) * 0.04)))
    miss_name = set(random.sample(range(len(raw)), int(len(raw) * 0.03)))
    for idx, p in enumerate(raw):
        if idx in miss_price: p.pop("price", None)
        if idx in miss_status: p.pop("status", None)
        if idx in miss_supplier: p.pop("supplier", None)
        if idx in miss_name: p.pop("name", None)

    with open(os.path.join(args.outdir, "products_2026.json"), "w") as f:
        json.dump(raw, f, indent=1)

    inv = []
    for p in products:
        qty = random.randint(0, 500) if random.random() > 0.06 else None
        inv.append({"sku_id": p["sku_id"], "stock_quantity": qty,
                    "warehouse": random.choice(WAREHOUSES),
                    "last_updated": f"2026-{random.randint(1,7):02d}-{random.randint(1,28):02d}"})
    inv.extend(dict(random.choice(inv)) for _ in range(int(len(inv) * 0.05)))
    random.shuffle(inv)
    with open(os.path.join(args.outdir, "inventory_updates_2026.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["sku_id", "stock_quantity", "warehouse", "last_updated"])
        w.writeheader()
        for r in inv:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})

    print(f"Wrote {len(raw)} product rows and {len(inv)} inventory rows to {args.outdir}")


if __name__ == "__main__":
    main()
