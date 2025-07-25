"""Support for the EPH Controls Ember themostats."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from pyephember2.pyephember2 import (
    EphEmber,
    ZoneMode,
    zone_current_temperature,
    zone_is_active,
    zone_is_hotwater,
    zone_mode,
    zone_name,
    zone_target_temperature,
)
import voluptuous as vol

from homeassistant.components.climate import (
    PLATFORM_SCHEMA as CLIMATE_PLATFORM_SCHEMA,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_PASSWORD,
    CONF_USERNAME,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

_LOGGER = logging.getLogger(__name__)

# Return cached results if last scan was less then this time ago
SCAN_INTERVAL = timedelta(seconds=120)

OPERATION_LIST = [HVACMode.HEAT_COOL, HVACMode.HEAT, HVACMode.OFF]

PLATFORM_SCHEMA = CLIMATE_PLATFORM_SCHEMA.extend(
    {vol.Required(CONF_USERNAME): cv.string, vol.Required(CONF_PASSWORD): cv.string}
)

EPH_TO_HA_STATE = {
    "AUTO": HVACMode.HEAT_COOL,
    "ON": HVACMode.HEAT,
    "OFF": HVACMode.OFF,
}

HA_STATE_TO_EPH = {value: key for key, value in EPH_TO_HA_STATE.items()}


def setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the ephember thermostat."""
    username = config.get(CONF_USERNAME)
    password = config.get(CONF_PASSWORD)

    try:
        ember = EphEmber(username, password)
    except RuntimeError:
        _LOGGER.error("Cannot login to EphEmber")

    try:
        homes = ember.get_zones()
    except RuntimeError:
        _LOGGER.error("Fail to get zones")
        return

    add_entities(
        EphEmberThermostat(ember, zone) for home in homes for zone in home["zones"]
    )


class EphEmberThermostat(ClimateEntity):
    """Representation of a EphEmber thermostat."""

    _attr_hvac_modes = OPERATION_LIST
    _attr_temperature_unit = UnitOfTemperature.CELSIUS

    def __init__(self, ember, zone) -> None:
        """Initialize the thermostat."""
        self._ember = ember
        self._zone_name = zone_name(zone)
        self._zone = zone
        self._attr_unique_id = zone["zoneid"]

        # hot water = true, is immersive device without target temperature control.
        self._hot_water = zone_is_hotwater(zone)

        self._attr_name = self._zone_name

        if self._hot_water:
            self._attr_target_temperature_step = None
        else:
            self._attr_target_temperature_step = 0.5
            self._attr_supported_features = (
                ClimateEntityFeature.TURN_OFF
                | ClimateEntityFeature.TURN_ON
                | ClimateEntityFeature.TARGET_TEMPERATURE
            )

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        return zone_current_temperature(self._zone)

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature we try to reach."""
        return zone_target_temperature(self._zone)

    @property
    def hvac_action(self) -> HVACAction:
        """Return current HVAC action."""
        if zone_is_active(self._zone):
            return HVACAction.HEATING

        return HVACAction.IDLE

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current operation ie. heat, cool, idle."""
        mode = zone_mode(self._zone)
        return self.map_mode_eph_hass(mode)

    def set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the operation mode."""
        mode = self.map_mode_hass_eph(hvac_mode)
        if mode is not None:
            self._ember.set_zone_mode(self._zone["zoneid"], mode)
        else:
            _LOGGER.error("Invalid operation mode provided %s", hvac_mode)

    def set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return

        if self._hot_water:
            return

        if temperature == self.target_temperature:
            return

        if temperature > self.max_temp or temperature < self.min_temp:
            return

        self._ember.set_zone_target_temperature(self._zone["zoneid"], temperature)

    @property
    def min_temp(self):
        """Return the minimum temperature."""
        # Hot water temp doesn't support being changed
        if self._hot_water:
            return zone_target_temperature(self._zone)

        return 5.0

    @property
    def max_temp(self):
        """Return the maximum temperature."""
        if self._hot_water:
            return zone_target_temperature(self._zone)

        return 35.0

    def update(self) -> None:
        """Get the latest data."""
        self._ember.get_zones()
        self._zone = self._ember.get_zone(self._zone["zoneid"])

    @staticmethod
    def map_mode_hass_eph(operation_mode):
        """Map from Home Assistant mode to eph mode."""
        return getattr(ZoneMode, HA_STATE_TO_EPH.get(operation_mode), None)

    @staticmethod
    def map_mode_eph_hass(operation_mode):
        """Map from eph mode to Home Assistant mode."""
        return EPH_TO_HA_STATE.get(operation_mode.name, HVACMode.HEAT_COOL)
