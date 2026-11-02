"""Deterministic entity reconciliation with field-level provenance (stdlib only)."""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

# Smaller numbers win; timestamps decide freshness within one source, not authority.
PRIORITY = {"registry": 0, "crm": 1, "partner": 2}
FIELDS = ("name", "city", "category")

# Source-specific adapters isolate upstream naming from the canonical model.
PROFILES = {
    "crm": {"id": "account_id", "name": "display_name", "city": "billing_city", "category": "segment"},
    "registry": {"id": "registration_id", "name": "legal_name", "city": "registered_city", "category": "classification"},
    "partner": {"id": "vendor_code", "name": "trading_name", "city": "location", "category": "product_group"},
}


def adapt(source, raw):
    if source not in PROFILES:
        raise ValueError("Unknown source")
    profile = PROFILES[source]
    record = {"source": source, "external_id": raw.get(profile["id"]),
              "supplier_id": raw.get("supplier_id"), "revision": raw.get("revision"),
              "fields": {field: raw.get(profile[field]) for field in FIELDS}}
    validate(record)
    return record


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS source_records (
            source TEXT NOT NULL, external_id TEXT NOT NULL,
            supplier_id TEXT NOT NULL, revision INTEGER NOT NULL,
            payload TEXT NOT NULL, digest TEXT NOT NULL,
            PRIMARY KEY(source, external_id));
        CREATE TABLE IF NOT EXISTS ingest_events (
            id INTEGER PRIMARY KEY, source TEXT, external_id TEXT,
            revision INTEGER, digest TEXT,
            accepted_at TEXT DEFAULT CURRENT_TIMESTAMP);
    """)
    return db


def validate(record):
    if not isinstance(record, dict):
        raise ValueError("Each record must be an object")
    if record.get("source") not in PRIORITY:
        raise ValueError("Unknown source")
    for key in ("external_id", "supplier_id"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            raise ValueError("Missing identity: " + key)
    if type(record.get("revision")) is not int or record["revision"] < 1:
        raise ValueError("revision must be a positive integer")
    fields = record.get("fields")
    if not isinstance(fields, dict) or set(fields) - set(FIELDS):
        raise ValueError("Unsupported fields")
    for value in fields.values():
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError("Field values must be nonempty strings or null")


def ingest(db, records):
    """Validate the entire batch, then apply atomically; reject ambiguous revisions."""
    records = list(records)
    for record in records:
        validate(record)
    counts = {"accepted": 0, "unchanged": 0, "stale": 0}
    with db:
        for record in records:
            payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(payload.encode()).hexdigest()
            key = (record["source"], record["external_id"])
            old = db.execute(
                "SELECT * FROM source_records WHERE source=? AND external_id=?", key
            ).fetchone()
            if old:
                if old["supplier_id"] != record["supplier_id"]:
                    raise ValueError("Identity reassignment requires manual review")
                if record["revision"] < old["revision"]:
                    counts["stale"] += 1
                    continue
                if record["revision"] == old["revision"]:
                    if digest != old["digest"]:
                        raise ValueError("Same revision has different content")
                    counts["unchanged"] += 1
                    continue
            db.execute("INSERT OR REPLACE INTO source_records VALUES (?,?,?,?,?,?)",
                       (*key, record["supplier_id"], record["revision"], payload, digest))
            db.execute("INSERT INTO ingest_events(source,external_id,revision,digest) VALUES (?,?,?,?)",
                       (*key, record["revision"], digest))
            counts["accepted"] += 1
    return counts


def reconcile(db, supplier_id):
    records = [json.loads(row[0]) for row in db.execute(
        "SELECT payload FROM source_records WHERE supplier_id=?", (supplier_id,))]
    if not records:
        raise ValueError("Unknown supplier")
    records.sort(key=lambda r: (PRIORITY[r["source"]], r["external_id"]))
    result = {"supplier_id": supplier_id, "attributes": {}}
    for field in FIELDS:
        candidates = [{"value": r["fields"][field], "source": r["source"],
                       "external_id": r["external_id"], "revision": r["revision"]}
                      for r in records if r["fields"].get(field) is not None]
        if candidates:
            result["attributes"][field] = {
                "selected": candidates[0], "candidates": candidates,
                "conflict": len({c["value"] for c in candidates}) > 1,
            }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=":memory:")
    parser.add_argument("--input", type=Path, default=Path(__file__).parent / "examples/source_feeds.json")
    parser.add_argument("--supplier", default="SUP-001")
    args = parser.parse_args()
    db = connect(args.db)
    try:
        feeds = json.loads(args.input.read_text())
        records = [adapt(feed["source"], feed["record"]) for feed in feeds]
        counts = ingest(db, records)
        print(json.dumps({"ingest": counts, "entity": reconcile(db, args.supplier)}, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
