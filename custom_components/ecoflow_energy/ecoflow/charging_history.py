"""Completed PowerPulse orders, retained by identity rather than poll count."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any


def identity(value: str) -> str:
    """Keep cloud identifiers out of entity IDs and the local ledger."""
    return hashlib.sha256(value.encode()).hexdigest()


def merge_orders(
    previous: dict[str, dict[str, Any]], rows: list[dict[str, Any]], serial: str
) -> dict[str, dict[str, Any]]:
    """Upsert validated completed orders; never assign from current selection.

    Only the allowlisted fields below leave the response. Order corrections
    replace the earlier entry, including corrections to vehicle attribution.
    Missing cloud history never deletes previously collected energy.
    """
    result = dict(previous)
    updates: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("sn") != serial:
            raise ValueError("Charging history contains another charger")
        end = row.get("endTime")
        if end in (None, "", 0, "0"):
            continue
        if not isinstance(end, str):
            raise ValueError("Invalid charging end time")
        datetime.fromisoformat(end)
        order = row.get("orderId")
        vehicle = row.get("vehicleId")
        energy = row.get("chargedEnergy")
        if (
            isinstance(order, bool)
            or not isinstance(order, (int, str))
            or not str(order).strip()
            or isinstance(vehicle, bool)
            or not isinstance(vehicle, (int, str))
            or not str(vehicle).strip()
            or type(energy) is not int
            or energy < 0
        ):
            raise ValueError("Invalid completed charging order")
        name = row.get("vehicleName")
        if name is not None and not isinstance(name, str):
            raise ValueError("Invalid vehicle name")
        key = identity(str(order))
        record = {
            "vehicle": identity(str(vehicle)),
            "other": str(vehicle) == "-1",
            "name": (name or "").strip()[:200],
            "energy_wh": energy,
            "ended": end,
        }
        if key in updates and updates[key] != record:
            raise ValueError("Conflicting duplicate charging order")
        updates[key] = record
    result.update(updates)
    return result


def vehicle_totals(orders: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Group stable vehicle identities; the latest completed record names them."""
    totals: dict[str, dict[str, Any]] = {}
    for record in sorted(orders.values(), key=lambda item: item["ended"]):
        key = record["vehicle"]
        total = totals.setdefault(
            key, {"energy_wh": 0, "sessions": 0, "name": "", "other": record["other"]}
        )
        total["energy_wh"] += record["energy_wh"]
        total["sessions"] += 1
        if record["name"]:
            total["name"] = record["name"]
    return totals
