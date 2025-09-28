# tools/build_pincode_index.py
import hashlib, json, os, sys
from pathlib import Path
import ujson as ujson

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "All_India_pincode_Boundary-cleaned.geojson"
NDJSON = ROOT / "data" / "pincodes.ndjson"
INDEX = ROOT / "data" / "pincode_index.json"
HASHF = ROOT / "data" / "pincode_hash.txt"

def file_hash(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def main():
    if not SRC.exists():
        print(f"ERROR: source file missing: {SRC}", file=sys.stderr)
        sys.exit(1)

    src_hash = file_hash(SRC)
    if HASHF.exists() and HASHF.read_text().strip() == src_hash and NDJSON.exists() and INDEX.exists():
        print("Index up-to-date. Skipping rebuild.")
        return

    print("Building NDJSON + index...")
    data = ujson.load(SRC.open("r", encoding="utf-8"))
    feats = data.get("features", [])
    # Sort by Pincode for stable offsets (not required, but nice)
    feats.sort(key=lambda f: f.get("properties", {}).get("Pincode", ""))

    # Write NDJSON and capture byte offsets
    index = {}
    with NDJSON.open("wb") as out:
        for feat in feats:
            pin = str(feat.get("properties", {}).get("Pincode", "")).strip()
            if not pin:
                continue
            off = out.tell()
            line = (ujson.dumps(feat, ensure_ascii=False) + "\n").encode("utf-8")
            out.write(line)
            index[pin] = off

    INDEX.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    HASHF.write_text(src_hash, encoding="utf-8")
    print(f"Wrote {NDJSON} and {INDEX}. Features indexed: {len(index)}")

if __name__ == "__main__":
    main()