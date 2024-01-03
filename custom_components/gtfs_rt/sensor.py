import datetime
import logging
from typing import Any
from enum import Enum

import voluptuous as vol

from homeassistant.components.sensor import (
    PLATFORM_SCHEMA as SENSOR_PLATFORM_SCHEMA,
    SensorEntity,
)
from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, CONF_NAME, UnitOfTime
from homeassistant.util import Throttle
import homeassistant.helpers.config_validation as cv
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from google.transit import gtfs_realtime_pb2
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
)

from .coordinator import ArrivalDetails, GtfsDataCoordinator
from .const import (
    ATTR_NAME,
    ATTR_STOP_ID,
    ATTR_DIRECTION_ID,
    ATTR_ROUTE_ID,
    ATTR_DUE_IN,
    ATTR_DUE_AT,
    ATTR_OCCUPANCY,
    ATTR_NEXT_UP,
    ATTR_NEXT_UP_DUE_IN,
    ATTR_NEXT_OCCUPANCY,
    ATTR_NEXT_ARRIVAL_LIST,
    CONF_SOURCE_HEADERS,
    CONF_SOURCE_QUERY_PARAMS,
    CONF_STOP_ID,
    CONF_DIRECTION_ID,
    CONF_ROUTE,
    CONF_DEPARTURES,
    CONF_SOURCES,
    CONF_SOURCE_TIMEZONE,
    CONF_SOURCE,
    CONF_SOURCE_TRIP_UPDATES_URL,
    CONF_SOURCE_VEHICLE_POSITIONS_URL,
    CONF_SOURCE_ALERTS_URL,
    CONF_SOURCE_STATIC_URL,
    CONF_SOURCE_STATIC_PATH,
    DOMAIN,
    TIME_STR_FORMAT,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "Next Bus"
ICON = "mdi:train"

# MIN_TIME_BETWEEN_UPDATES = datetime.timedelta(seconds=10)


# def validate_source_name(config):
#     source_names = [source[CONF_SOURCE] for source in config[CONF_SOURCES]]
#     for departure in config.get(CONF_DEPARTURES, []):
#         if departure[CONF_SOURCE] not in source_names:
#             raise vol.Invalid(
#                 f"The source_name {departure[CONF_SOURCE]} in departures must match a source_name in sources"
#             )
#     return config


PLATFORM_SCHEMA = SENSOR_PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_SOURCES): [
            {
                vol.Required(CONF_SOURCE): cv.string,
                vol.Optional(CONF_SOURCE_HEADERS, default={}): {cv.string: cv.string},
                vol.Optional(CONF_SOURCE_QUERY_PARAMS, default={}): {
                    cv.string: cv.string
                },
                vol.Optional(CONF_SOURCE_TIMEZONE): cv.string,
                vol.Required(CONF_SOURCE_TRIP_UPDATES_URL): cv.string,
                vol.Required(CONF_SOURCE_VEHICLE_POSITIONS_URL): cv.string,
                vol.Required(CONF_SOURCE_ALERTS_URL): cv.string,
                vol.Exclusive(
                    CONF_SOURCE_STATIC_URL, CONF_SOURCE_STATIC_PATH
                ): cv.string,
            }
        ],
        vol.Optional(CONF_DEPARTURES): [
            {
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
                vol.Required(CONF_SOURCE): cv.string,
                vol.Required(CONF_STOP_ID): cv.string,
                vol.Required(CONF_DIRECTION_ID): vol.Coerce(int),
                vol.Required(CONF_ROUTE): cv.string,
            }
        ],
    }
)


def due_in_minutes(arrival_time: datetime.datetime) -> int:
    """Get the remaining minutes from now until a given datetime object."""
    diff = arrival_time - datetime.datetime.now()
    mins = int(diff.total_seconds() / 60)
    return mins


def setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    add_devices: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType = None,
) -> None:
    """Get the Dublin public transport sensor."""
    hass.data[DOMAIN] = {}
    for source in config.get(CONF_SOURCES):
        source_name = source[CONF_SOURCE]
        hass.data[DOMAIN][source_name] = GtfsDataCoordinator(
            hass,
            source_name,
            source.get(CONF_SOURCE_STATIC_URL),
            source.get(CONF_SOURCE_STATIC_PATH),
            source.get(CONF_SOURCE_TRIP_UPDATES_URL),
            source.get(CONF_SOURCE_VEHICLE_POSITIONS_URL),
            source.get(CONF_SOURCE_ALERTS_URL),
            source.get(CONF_SOURCE_HEADERS, {}),
        )

    sensors = []
    for departure in config.get(CONF_DEPARTURES):
        attributes = {
            ATTR_STOP_ID: departure.get(CONF_STOP_ID),
            ATTR_DIRECTION_ID: departure.get(CONF_DIRECTION_ID),
            ATTR_ROUTE_ID: departure.get(CONF_ROUTE),
            ATTR_NAME: departure.get(CONF_NAME),
        }
        source_name = departure.get(CONF_SOURCE)
        coordinator = hass.data[DOMAIN][source_name]
        sensors.append(GtfsSensor(coordinator, attributes))

    add_devices(sensors, True)


class GtfsSensor(CoordinatorEntity, SensorEntity):
    """Implementation of a public transport sensor."""

    # _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: GtfsDataCoordinator, attributes: dict):
        """Initialize Gtfs sensor."""
        super().__init__(coordinator)

        self._coordinator = coordinator
        # TODO: remove cast
        self._stop: str = str(attributes.get(ATTR_STOP_ID))
        self._route: str | None = attributes.get(ATTR_ROUTE_ID)
        self._direction: int | None = attributes.get(ATTR_DIRECTION_ID)

        self._attr_name = attributes.get(CONF_NAME)
        self._attr_icon = ICON
        self._attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def _get_next_arrivals(self) -> list["ArrivalDetails"]:
        """Get the next arrivals from coordinator data."""
        return self._coordinator.get_next_arrivals(
            self._stop, self._route, self._direction
        )

    @property
    def state(self) -> str | int:
        """Return the state of the sensor."""
        next_arrivals = self._get_next_arrivals()
        if len(next_arrivals) > 0:
            return due_in_minutes(next_arrivals[0].arrival_time)
        else:
            return "-"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        next_arrivals = self._get_next_arrivals()
        attrs = {
            ATTR_DUE_IN: self.state,
            ATTR_NEXT_ARRIVAL_LIST: [
                due_in_minutes(arrival.arrival_time) for arrival in next_arrivals
            ],
            ATTR_STOP_ID: self._stop,
            ATTR_ROUTE_ID: self._route,
            ATTR_DIRECTION_ID: self._direction,
        }

        if len(next_arrivals) > 0:
            attrs[ATTR_DUE_AT] = (
                next_arrivals[0].arrival_time.strftime(TIME_STR_FORMAT)
                if len(next_arrivals) > 0
                else "-"
            )
            attrs[ATTR_OCCUPANCY] = next_arrivals[0].occupancy
            if next_arrivals[0].position:
                attrs[ATTR_LATITUDE] = next_arrivals[0].position.latitude
                attrs[ATTR_LONGITUDE] = next_arrivals[0].position.longitude
        if len(next_arrivals) > 1:
            attrs[ATTR_NEXT_UP] = (
                next_arrivals[1].arrival_time.strftime(TIME_STR_FORMAT)
                if len(next_arrivals) > 1
                else "-"
            )
            attrs[ATTR_NEXT_UP_DUE_IN] = (
                due_in_minutes(next_arrivals[1].arrival_time)
                if len(next_arrivals) > 1
                else "-"
            )
            attrs[ATTR_NEXT_OCCUPANCY] = next_arrivals[1].occupancy
        return attrs

    def update(self) -> None:
        """Get the latest data from opendata.ch and update the states."""
        self.data.update()
