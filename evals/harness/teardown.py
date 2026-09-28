"""Delete what one case-run created.

`galtea products delete` cascades to specifications, datasets, connections, monitors,
runs with their evaluations, and versions. Metrics belong to the organization, so they
survive it, and metric names are unique per organization: a later run that picks the
same name would collide. Delete them first, while the specifications still link them.
"""

from __future__ import annotations

import sys
from typing import Any

from collect import galtea, product_metrics


def teardown(pid: str) -> list[str]:
    log: list[str] = []
    metrics: list[dict[str, Any]] = []
    try:
        specs = (
            galtea("specifications", "list", "--product-ids", pid, "--limit", "500")
            or []
        )
        # Superseded revisions belong to the run too, so delete them with the live ones.
        metrics = product_metrics(pid, [s["id"] for s in specs], include_legacy=True)
    except Exception as e:  # noqa: BLE001 - the product delete below must still run
        log.append(f"FAILED listing metrics of {pid}: {e}")
    for m in metrics:
        try:
            galtea("metrics", "delete", m["id"])
            log.append(f"deleted metric {m['id']} ({m.get('name')})")
        except Exception as e:  # noqa: BLE001
            log.append(f"FAILED metric {m['id']}: {e}")
    galtea("products", "delete", pid)
    log.append(f"deleted product {pid}")
    return log


if __name__ == "__main__":
    for pid in sys.argv[1:]:
        print("\n".join(teardown(pid)))
