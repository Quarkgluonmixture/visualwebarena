"""Replace the website placeholders with website domains from env_config
Generate the test data"""
import hashlib
import json
import os
import shutil

from browser_env.env_config import *


def main() -> None:
    DATASET = os.environ["DATASET"]
    if DATASET == "webarena":
        print("DATASET: webarena")
        print(f"REDDIT: {REDDIT}")
        print(f"SHOPPING: {SHOPPING}")
        print(f"SHOPPING_ADMIN: {SHOPPING_ADMIN}")
        print(f"GITLAB: {GITLAB}")
        print(f"WIKIPEDIA: {WIKIPEDIA}")
        print(f"MAP: {MAP}")
        print(f"HOMEPAGE: {HOMEPAGE}")
        inp_paths = ["config_files/wa/test_webarena.raw.json"]
        replace_map = {
            "__REDDIT__": REDDIT,
            "__SHOPPING__": SHOPPING,
            "__SHOPPING_ADMIN__": SHOPPING_ADMIN,
            "__GITLAB__": GITLAB,
            "__WIKIPEDIA__": WIKIPEDIA,
            "__MAP__": MAP,
            "__HOMEPAGE__": HOMEPAGE,
        }
    elif DATASET == "visualwebarena":
        print("DATASET: visualwebarena")
        print(f"CLASSIFIEDS: {CLASSIFIEDS}")
        print(f"REDDIT: {REDDIT}")
        print(f"SHOPPING: {SHOPPING}")
        print(f"HOMEPAGE: {HOMEPAGE}")
        inp_paths = [
            "config_files/vwa/test_classifieds.raw.json", "config_files/vwa/test_shopping.raw.json", "config_files/vwa/test_reddit.raw.json",
        ]
        replace_map = {
            "__REDDIT__": REDDIT,
            "__SHOPPING__": SHOPPING,
            "__WIKIPEDIA__": WIKIPEDIA,
            "__CLASSIFIEDS__": CLASSIFIEDS,
            "__HOMEPAGE__": HOMEPAGE,
        }
    else:
        raise ValueError(f"Dataset not implemented: {DATASET}")

    # /stress A1.18-re (B-577 P0-1-AB Claude+codex, 2026-05-17): clean-rebuild
    # output_dir BEFORE writing. Pre-fix used `os.makedirs(exist_ok=True)` and
    # never deleted stale `*.json` → if a raw template shortened or a replayer
    # switched dataset versions, old high-numbered task files remained on disk
    # → any directory-glob consumer (P79 task loader walks
    # `config_files/vwa/test_classifieds/`) would mix versioned tasks.
    # Idempotent rebuild guarantees split output equals current raw template.
    #
    # /stress A1.18-re (B-588 P1-9-B codex, 2026-05-17): write JSON with
    # explicit UTF-8 + LF newline + sort_keys for byte-stable derivation across
    # OS/locale. Pre-fix `open(..., "r"/"w")` + bare `json.dump(indent=2)` was
    # locale + Windows-CRLF sensitive → different replayer hosts produced
    # different file SHAs even though task content identical.
    manifest: list[dict] = []
    for inp_path in inp_paths:
        output_dir = inp_path.replace('.raw.json', '')
        if os.path.isdir(output_dir):
            shutil.rmtree(output_dir)
        os.makedirs(output_dir, exist_ok=True)
        with open(inp_path, "r", encoding="utf-8") as f:
            raw = f.read()
        raw_sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        for k, v in replace_map.items():
            raw = raw.replace(k, v)

        with open(inp_path.replace(".raw", ""), "w", encoding="utf-8", newline="\n") as f:
            f.write(raw)
        data = json.loads(raw)
        per_file_hashes = []
        for idx, item in enumerate(data):
            split_path = os.path.join(output_dir, f"{idx}.json")
            with open(split_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(item, f, indent=2, ensure_ascii=False, sort_keys=True)
                f.write("\n")  # trailing newline for POSIX-friendly diff
            with open(split_path, "rb") as f:
                per_file_hashes.append(hashlib.sha256(f.read()).hexdigest())
        manifest.append({
            "inp_path": inp_path,
            "output_dir": output_dir,
            "raw_sha256": raw_sha256,
            "generated_count": len(data),
            "first_split_sha256": per_file_hashes[0] if per_file_hashes else None,
            "last_split_sha256": per_file_hashes[-1] if per_file_hashes else None,
        })
        # B-577 assertion: every raw entry materialized.
        assert len(data) == len(per_file_hashes), (
            f"generation mismatch for {inp_path}: "
            f"{len(data)} raw entries vs {len(per_file_hashes)} written files"
        )

    # /stress A1.18-re (B-577 + B-588): emit generation_manifest.json so
    # paper-grade OSF replayers can verify byte-stable split output without
    # re-running the script.
    manifest_path = "config_files/generation_manifest.json"
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"dataset": DATASET, "replace_map": replace_map, "outputs": manifest}, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    print(f"Wrote generation manifest: {manifest_path}")


if __name__ == "__main__":
    main()
