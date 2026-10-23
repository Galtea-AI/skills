"""Collect what the platform holds for the products one case-run created, as full rows.

The prompt does not name the product, as a real user would not. So the products a
run created are the ones the current user created after the run started, minus the
ones that already existed then. Everything else hangs off a product, so its id reaches it all
(test cases go through their datasets: `test-cases list` filters by dataset only).
Metrics belong to the organization, not the product, and `metrics list --product-ids`
returns only metrics with evaluations in that product's sessions. A metric generated
from a specification has none yet, so metrics are the union of that filter and the
metrics linked to the product's specifications. Teardown deletes them separately.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

TIMEOUT_S = 60
LIMIT = "500"
# Lists report superseded revisions unless told not to, and every count a case checks is
# of what is live now.
LIVE = "--include-legacy=false"
# The entity lists collect() returns: the keys a case's `expect` may name.
ENTITIES = (
    "products",
    "versions",
    "endpoint_connections",
    "specifications",
    "datasets",
    "test_cases",
    "metrics",
    "sessions",
    "evaluations",
)


def galtea(*args: str) -> Any:
    # stdin closed: body-taking commands otherwise block in a non-TTY harness.
    out = subprocess.run(
        ["galtea", *args, "-o", "json"],
        stdin=subprocess.DEVNULL,
        check=False,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
    )
    if out.returncode != 0:
        raise RuntimeError(
            f"galtea {' '.join(args)} failed ({out.returncode}): {out.stderr.strip()[:300]}"
        )
    return json.loads(out.stdout or "null")


def current_user_id() -> str:
    return galtea("auth", "get-current-user")["id"]


def my_products_since(since_iso: str, user_id: str) -> list[dict[str, Any]]:
    rows = (
        galtea("products", "list", "--from-created-at", since_iso, "--limit", LIMIT)
        or []
    )
    return [r for r in rows if r.get("userId") == user_id]


def product_metrics(
    pid: str, spec_ids: list[str], include_legacy: bool = False
) -> list[dict[str, Any]]:
    legacy = [] if include_legacy else [LIVE]
    rows = (
        galtea("metrics", "list", "--product-ids", pid, "--limit", LIMIT, *legacy) or []
    )
    if spec_ids:
        rows += (
            galtea(
                "metrics",
                "list",
                "--specification-ids",
                ",".join(spec_ids),
                "--limit",
                LIMIT,
                *legacy,
            )
            or []
        )
    return list({m["id"]: m for m in rows}.values())


def collect(products: list[dict[str, Any]]) -> dict[str, Any]:
    """One state for all the run's products; entity lists are concatenated across them."""
    if not products:
        return {"products": [], "product": None}
    ids = ",".join(p["id"] for p in products)
    by_product = lambda noun: (
        galtea(noun, "list", "--product-ids", ids, "--limit", LIMIT) or []
    )
    specifications = by_product("specifications")
    datasets = by_product("datasets")
    test_cases: list[dict[str, Any]] = []
    if datasets:
        test_cases = (
            galtea(
                "test-cases",
                "list",
                "--test-ids",
                ",".join(d["id"] for d in datasets),
                "--limit",
                LIMIT,
                LIVE,
            )
            or []
        )
    metrics: list[dict[str, Any]] = []
    for p in products:
        metrics += product_metrics(
            p["id"], [s["id"] for s in specifications if s.get("productId") == p["id"]]
        )
    return {
        "products": products,
        "product": products[0],
        "versions": by_product("versions"),
        "endpoint_connections": by_product("endpoint-connections"),
        "specifications": specifications,
        "datasets": datasets,
        "test_cases": test_cases,
        "metrics": list({m["id"]: m for m in metrics}.values()),
        "sessions": by_product("sessions"),
        "evaluations": by_product("evaluations"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("product_id", nargs="+")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    state = collect([galtea("products", "get", pid) for pid in a.product_id])
    a.out.write_text(json.dumps(state, indent=1, default=str))
    counts = {k: len(v) for k, v in state.items() if isinstance(v, list)}
    print(json.dumps({"product_found": state["product"] is not None, **counts}))


if __name__ == "__main__":
    main()
