# tools/extract_by_pincode.py
import json, os, sys
from pathlib import Path
import ujson as ujson

ROOT = Path(__file__).resolve().parents[1]
NDJSON = ROOT / "data" / "pincodes.ndjson"
INDEX = ROOT / "data" / "pincode_index.json"
DIST = ROOT / "dist"
DIST.mkdir(exist_ok=True)

def gh_set_output(name, value):
    # Compatible with both new and old GH Actions
    out_path = os.environ.get("GITHUB_OUTPUT")
    if out_path:
        with open(out_path, "a", encoding="utf-8") as f:
            print(f"{name}={value}", file=f)
    else:
        # fallback
        print(f"::set-output name={name}::{value}")

def parse_pins():
    # Priority: workflow input override -> PINCODES -> PINCODE
    override = os.getenv("INPUT_OVERRIDE_PINS", "").strip()  # populated by workflow_dispatch
    if override:
        pins = override
    else:
        pins = os.getenv("PINCODES") or os.getenv("PINCODE") or ""
    # normalize
    pins = [p.strip() for p in pins.replace(" ", "").split(",") if p.strip()]
    return pins

def read_feature_at(offset: int):
    with NDJSON.open("rb") as f:
        f.seek(offset)
        line = f.readline()
    return ujson.loads(line.decode("utf-8"))

def main():
    if not NDJSON.exists() or not INDEX.exists():
        print("ERROR: Index missing. Run tools/build_pincode_index.py first.", file=sys.stderr)
        sys.exit(1)

    index = json.loads(INDEX.read_text(encoding="utf-8"))
    pins = parse_pins()
    if not pins:
        print("ERROR: No pincode provided (PINCODES/PINCODE/workflow input).", file=sys.stderr)
        sys.exit(1)

    found = []
    first_bbox = ""
    first_pin = ""

    for pin in pins:
        off = index.get(str(pin))
        if off is None:
            print(f"WARNING: PIN {pin} not found in index.", file=sys.stderr)
            continue
        feat = read_feature_at(off)
        # Write artifacts
        (DIST / f"pincode_{pin}.geojson").write_text(json.dumps(feat, ensure_ascii=False), encoding="utf-8")
        props = feat.get("properties", {})
        (DIST / f"pincode_{pin}_properties.json").write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")

        bbox = feat.get("bbox")
        if not bbox:
            # derive bbox quickly from polygon coords if present (no shapely)
            try:
                coords = feat["geometry"]["coordinates"][0]
                xs = [c[0] for c in coords]
                ys = [c[1] for c in coords]
                bbox = [min(xs), min(ys), max(xs), max(ys)]
            except Exception:
                bbox = []
        if bbox:
            (DIST / f"pincode_{pin}_bbox.json").write_text(json.dumps(bbox), encoding="utf-8")

        found.append(pin)
        if not first_pin:
            first_pin = pin
            first_bbox = ",".join(map(str, bbox)) if bbox else ""

    # Expose outputs for downstream steps
    if first_pin:
        gh_set_output("pin", first_pin)
        gh_set_output("bbox", first_bbox)
    (DIST / "pins_found.txt").write_text(",".join(found), encoding="utf-8")

    # Non-zero exit if none found (to fail early)
    if not found:
        print("ERROR: None of the requested pincodes were found.", file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()