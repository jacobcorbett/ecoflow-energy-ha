"""Explicit grid connection beats energized backup; all inputs are synthetic."""

from __future__ import annotations

import pytest
from ecoflow_energy.ecoflow.parsers.powerocean import parse_powerocean_http_quota
from ecoflow_energy.ecoflow.parsers.powerocean_proto import (
    flatten_heartbeat,
    remap_bp_keys,
    remap_ems_state_keys,
)


@pytest.mark.parametrize("raw,expected", [(0, "on_grid"), (1, "off_grid"), (7, None)])
def test_grid_state_http_and_proto_agree(raw: int, expected: str | None) -> None:
    assert (
        parse_powerocean_http_quota({"ems_change_report.sysGridSta": raw})[
            "grid_status"
        ]
        == expected
    )
    assert (
        remap_bp_keys({"sys_grid_sta": raw}, {}, "HJ31TEST00000001")["grid_status"]
        == expected
    )


def test_outage_survives_backup_voltage_and_restores() -> None:
    state = remap_bp_keys(
        {"sys_grid_sta": 1, "grid_is_energized": True}, {}, "HJ31TEST00000001"
    )
    state.update(
        flatten_heartbeat(
            {
                "pcs_a_phase": {"vol": 230.0},
                "pcs_load_info": [{"vol": 230.0, "pwr": 500.0}],
            }
        )
    )
    assert state["grid_status"] == "off_grid"
    state.update(remap_bp_keys({"sys_grid_sta": 0}, {}, "HJ31TEST00000001"))
    assert state["grid_status"] == "on_grid"
    state.update(remap_bp_keys({"sys_grid_sta": 9}, {}, "HJ31TEST00000001"))
    assert state["grid_status"] is None


def test_absence_and_command17_do_not_invent_grid_state() -> None:
    assert "grid_status" not in parse_powerocean_http_quota({})
    assert "grid_status" not in remap_bp_keys({}, {}, "HJ31TEST00000001")
    assert "grid_status" not in remap_ems_state_keys({"sys_grid_sta": 0})
    assert "grid_status" not in flatten_heartbeat({"pcs_a_phase": {"vol": 0}})
