"""HA persistence and entity lifecycle for per-profile energy."""

from __future__ import annotations

from collections.abc import Iterable
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ecoflow_energy.charging_history import (
    ChargingHistoryCoordinator,
    VehicleEnergySensor,
    async_setup_charging_history,
)
from custom_components.ecoflow_energy.const import DOMAIN
from custom_components.ecoflow_energy.ecoflow.charging_history import identity

SERIAL = "C371TEST0001"


def record(oid="a", vid="profile-a", energy=16004):
    return {
        "sn": SERIAL,
        "orderId": oid,
        "vehicleId": vid,
        "vehicleName": "Example EV",
        "chargedEnergy": energy,
        "endTime": "2026-10-02 12:00:00",
    }


def entry(hass):
    config = MockConfigEntry(
        domain=DOMAIN, data={"email": "dummy@example.com", "password": "dummy"}
    )
    config.add_to_hass(hass)
    return config


async def test_restart_does_not_double_count_and_save_failure_is_atomic(
    hass: HomeAssistant,
):
    config = entry(hass)
    coord = ChargingHistoryCoordinator(hass, config, SERIAL)
    coord.api.get_powerpulse_orders = AsyncMock(return_value=[record()])
    await coord.async_refresh()
    assert coord.data[identity("profile-a")]["energy_wh"] == 16004
    restored = ChargingHistoryCoordinator(hass, config, SERIAL)
    await restored.async_restore()
    restored.api.get_powerpulse_orders = AsyncMock(return_value=[record()])
    await restored.async_refresh()
    assert restored.data == coord.data
    restored.api.get_powerpulse_orders.return_value = [
        record(),
        record("b", "profile-b", 2000),
    ]
    with patch.object(restored.store, "async_save", side_effect=OSError):
        await restored.async_refresh()
    assert not restored.last_update_success
    assert restored.data == coord.data
    assert restored.orders == coord.orders
    await restored.async_refresh()
    assert restored.data[identity("profile-b")]["energy_wh"] == 2000
    await coord.async_shutdown()
    await restored.async_shutdown()


async def test_discovery_units_corrections_and_failure(hass: HomeAssistant):
    config = entry(hass)
    added: list[Entity] = []

    def add(new_entities: Iterable[Entity], update_before_add: bool = False) -> None:
        added.extend(new_entities)

    with patch(
        "custom_components.ecoflow_energy.charging_history.AppApiClient.get_powerpulse_orders",
        new_callable=AsyncMock,
        return_value=[record()],
    ):
        coord = await async_setup_charging_history(
            hass,
            config,
            SERIAL,
            DeviceInfo(identifiers={(DOMAIN, SERIAL)}),
            add,
        )
    sensor = added[0]
    assert isinstance(sensor, VehicleEnergySensor)
    assert sensor.native_value == 16.004
    assert sensor.state_class == "total"
    uid = sensor.unique_id
    coord.api.get_powerpulse_orders = AsyncMock(
        return_value=[record(energy=15000), record("b", "-1", 1000)]
    )
    await coord.async_refresh()
    assert len(added) == 2
    assert sensor.native_value == 15
    assert sensor.unique_id == uid
    assert isinstance(added[1], VehicleEnergySensor)
    assert added[1].translation_key == "other_vehicle_energy"
    coord.api.get_powerpulse_orders.side_effect = ValueError("incomplete")
    await coord.async_refresh()
    assert not sensor.available
    assert sensor.native_value == 15
    coord.api.get_powerpulse_orders.side_effect = None
    # A correction that moves the sole order off a vehicle clears its total.
    coord.api.get_powerpulse_orders.return_value = [record(vid="profile-b")]
    await coord.async_refresh()
    assert sensor.native_value == 0
    await coord.async_shutdown()


async def test_history_only_c371_setup_and_unload(hass: HomeAssistant):
    """A C371 needs no unverified live parser/control connection for history."""
    config = MockConfigEntry(
        domain=DOMAIN,
        data={
            "auth_method": "app",
            "mode": "enhanced",
            "email": "dummy@example.com",
            "password": "dummy",
            "powerpulse_vehicle_energy": True,
            "devices": [{"sn": SERIAL, "product_name": "", "device_type": "unknown"}],
        },
    )
    config.add_to_hass(hass)
    with patch(
        "custom_components.ecoflow_energy.charging_history.AppApiClient.get_powerpulse_orders",
        new_callable=AsyncMock,
        return_value=[record()],
    ) as read:
        assert await hass.config_entries.async_setup(config.entry_id)
        await hass.async_block_till_done()
        read.assert_awaited_once()
        states = [
            s
            for s in hass.states.async_all("sensor")
            if "completed_sessions" in s.attributes
        ]
        assert len(states) == 1
        assert states[0].state == "16.004"
        assert states[0].attributes["unit_of_measurement"] == "kWh"
        assert not hass.states.async_all("button")
        assert not hass.states.async_all("number")
        assert not hass.states.async_all("select")
        assert await hass.config_entries.async_unload(config.entry_id)
        await hass.async_block_till_done()


async def test_option_is_opt_in_and_persists(hass: HomeAssistant):
    from homeassistant.data_entry_flow import FlowResultType

    config = MockConfigEntry(
        domain=DOMAIN,
        data={
            "auth_method": "app",
            "mode": "enhanced",
            "email": "dummy@example.com",
            "password": "dummy",
            "devices": [{"sn": SERIAL, "product_name": "", "device_type": "unknown"}],
        },
    )
    config.add_to_hass(hass)
    with patch(
        "custom_components.ecoflow_energy.config_flow_options._async_fetch_app_devices",
        new_callable=AsyncMock,
        return_value=[],
    ):
        form = await hass.config_entries.options.async_init(config.entry_id)
        assert form["type"] is FlowResultType.FORM
        assert form["data_schema"] is not None
        schema = form["data_schema"].schema
        option = next(k for k in schema if k.schema == "powerpulse_vehicle_energy")
        assert option.default() is False
        saved = await hass.config_entries.options.async_configure(
            form["flow_id"],
            {
                "mode": "enhanced",
                "devices": [SERIAL],
                "powerpulse_vehicle_energy": True,
            },
        )
        assert saved["type"] is FlowResultType.CREATE_ENTRY
        assert config.data["powerpulse_vehicle_energy"] is True
