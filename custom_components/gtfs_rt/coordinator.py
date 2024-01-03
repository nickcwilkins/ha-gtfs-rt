import asyncio
from datetime import datetime, timedelta
from enum import Enum
import logging

from urllib.parse import urlparse
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from google.transit import gtfs_realtime_pb2
from .api import async_get_feed

from .const import (
    TIME_STR_FORMAT,
)

_LOGGER = logging.getLogger(__name__)

_gtfs_file_primarykey_mapping = {
    "agency.txt": "agency_id",
    "stops.txt": "stop_id",
    "routes.txt": "route_id",
    "trips.txt": "trip_id",
    "stop_times.txt": None,
    "calendar.txt": None,
    "calendar_dates.txt": None,
    "fare_attributes.txt": None,
    "fare_rules.txt": None,
    "timeframes.txt": None,
    "fare_media.txt": None,
    "fare_products.txt": None,
    "fare_leg_rules.txt": None,
    "fare_transfer_rules.txt": None,
    "areas.txt": None,
    "stop_areas.txt": None,
    "networks.txt": None,
    "route_networks.txt": None,
    "shapes.txt": None,
    "frequencies.txt": None,
    "transfers.txt": None,
    "pathways.txt": None,
    "levels.txt": None,
    "translations.txt": None,
    "feed_info.txt": None,
    "attributions.txt": None,
}


# type var for DataUpdateCoordinator
from typing import NamedTuple, TypeVar, Mapping

_GtfsData = dict[str, dict[int, dict[str, list["ArrivalDetails"]]]]


class GtfsDataCoordinator(DataUpdateCoordinator[_GtfsData]):
    """
    A class for fetching and storing GTFS data
    """

    def __init__(
        self,
        hass: HomeAssistant,
        source_name: str,
        static_url: str | None,
        static_path: str | None,
        route_update_url: str,
        vehicle_position_url: str,
        alerts_url: str,
        headers: dict,
    ):
        self.static_url = static_url
        self.headers = headers
        self.route_update_url = route_update_url
        self.vehicle_position_url = vehicle_position_url
        self.alerts_url = alerts_url
        self.vehicle_positions: dict[str, _VehiclePosition] = {}
        self.vehicle_trips: dict[str, str] = {}
        self.vehicle_occupancy: dict[str, int] = {}
        self.data: _GtfsData = {}

        super().__init__(
            hass,
            _LOGGER,
            name=source_name,
            update_interval=timedelta(seconds=30),
        )

    async def _async_update_data(self) -> _GtfsData:
        _LOGGER.debug(
            f"updating gtfs rt data {self.route_update_url} {self.vehicle_position_url} {self.alerts_url} "
        )
        self.feed = await async_get_feed(
            self.route_update_url,
            self.vehicle_position_url,
            self.alerts_url,
            self.headers,
        )

        data: _GtfsData = {}
        for entity in self.feed.entity:
            if entity.HasField("trip_update"):
                route_id: str = entity.trip_update.trip.route_id
                direction_id: int = entity.trip_update.trip.direction_id
                trip_id: str = entity.trip_update.trip.trip_id
                trip_vehicle_id: str = entity.trip_update.vehicle.id
                # Get link between vehicle_id from trip_id from vehicles positions if needed
                vehicle_id: str | None = trip_vehicle_id or self.vehicle_trips.get(
                    trip_id
                )

                if route_id not in data:
                    data[route_id] = {}

                if direction_id not in data[route_id]:
                    data[route_id][direction_id] = {}

                stopCount = len(entity.trip_update.stop_time_update)
                destination = "Unknown"
                if stopCount > 0:
                    destination = entity.trip_update.stop_time_update[
                        stopCount - 1
                    ].stop_id

                for stop_time_update in entity.trip_update.stop_time_update:
                    stop_id: str = stop_time_update.stop_id
                    if not data[route_id][direction_id].get(stop_id):
                        data[route_id][direction_id][stop_id] = []

                    stop_timestamp = (
                        stop_time_update.arrival
                        and stop_time_update.arrival.time
                        or stop_time_update.departure.time
                    )
                    stop_datetime = datetime.fromtimestamp(stop_timestamp)
                    # Keep only future arrival.time (gtfs data can give past arrival.time, which is useless and show negative time as result)
                    if stop_datetime > datetime.now():
                        position = (
                            self.vehicle_positions.get(vehicle_id)
                            if vehicle_id
                            else None
                        )
                        occupancy = (
                            self.vehicle_occupancy.get(vehicle_id)
                            if vehicle_id
                            else None
                        )
                        details = ArrivalDetails(
                            stop_datetime,
                            destination,
                            position,
                            occupancy,
                        )
                        data[route_id][direction_id][stop_id].append(details)

        # Sort by arrival time
        for route_id in data:
            for direction_id in data[route_id]:
                for stop_id in data[route_id][direction_id]:
                    data[route_id][direction_id][stop_id].sort(
                        key=lambda t: t.arrival_time
                    )

        return data

    def get_next_arrivals(
        self, stop_id: str, route_id: str | None, direction_id: int | None
    ) -> list["ArrivalDetails"]:
        """
        Get the next arrivals to a stop_id, optionally filtered by route_id and direction_id
        """
        arrivals: list["ArrivalDetails"] = []

        filtered: _GtfsData = {}
        if route_id:
            if route_id in self.data:
                filtered[route_id] = self.data[route_id]
            else:
                return arrivals
        else:
            filtered = self.data

        for routes in filtered.values():
            if direction_id:
                if direction_id in routes:
                    arrivals.extend(routes[direction_id][stop_id])

        arrivals.sort(key=lambda t: t.arrival_time)

        return arrivals

    def _update_vehicle_positions(self) -> None:
        feed = self.feed
        positions = {}
        vehicle_trips = {}
        occupancy = {}

        for entity in feed.entity:
            vehicle = entity.vehicle
            if not vehicle.trip.route_id:
                # Vehicle is not in service
                continue
            positions[vehicle.vehicle.id] = vehicle.position
            vehicle_trips[vehicle.trip.trip_id] = vehicle.vehicle.id
            occupancy[vehicle.vehicle.id] = vehicle.occupancy_status

        self.vehicle_trips = vehicle_trips
        self.vehicle_positions = positions
        self.vehicle_occupancy = occupancy


_VehiclePosition = NamedTuple(
    "_VehiclePosition",
    [("latitude", float), ("longitude", float), ("bearing", float), ("speed", float)],
)


class ArrivalDetails:
    """
    ArrivalDetails provides information about a vehicle that will arrive at (or has departed) a stop
    """

    def __init__(
        self,
        arrival_time: datetime,
        destination: str,
        position: _VehiclePosition | None,
        occupancy: int | None,
    ) -> None:
        self.arrival_time = arrival_time
        self.destination = destination
        self.position = position
        self.occupancy = occupancy

    # def LoadData(self) -> None:
    #     if not os.path.exists(self.static_data_path):
    #         os.makedirs(self.static_data_path)
    #         print(f'fetching gtfs static dataset from {self.url}')
    #         response = requests.get(self.url, headers={ 'api_key': '67b917d2eafc4c7786d59aa34b710007' })
    #         temp = tempfile.NamedTemporaryFile(delete=False)
    #         temp.write(response.content)
    #         temp.flush()

    #         with zipfile.ZipFile(temp, 'r') as zip_ref:
    #             zip_ref.extractall(self.static_data_path)
    #     else:
    #         print(f'using existing gtfs static dataset fetched from {self.url}')

    #     for filename in os.listdir(self.static_data_path):
    #         if filename in _gtfs_file_primarykey_mapping:
    #             self.data[filename] = {}
    #         with open(os.path.join(self.static_data_path, filename), 'r') as f:
    #             reader = csv.reader(f)
    #             header = next(reader)
    #             primary_key_name = _gtfs_file_primarykey_mapping[filename] or header[0]
    #             primary_key_index = header.index(primary_key_name)

    #             for row in reader:
    #                 self.data[filename][row[primary_key_index]] = dict(zip(header, row))
    #     else:
    #         print(f'Unknown GTFS file: {filename}')

    # def get_route(self, route_id: str) -> dict:
    #     if 'routes.txt' not in self.data:
    #         raise Exception('routes.txt not loaded')
    #     if route_id not in self.data['routes.txt']:
    #         raise Exception(f'route_id {route_id} not found in routes.txt')

    #     return [self.data['routes.txt'][route_id]['route_short_name'] for route_id in self.data['routes.txt']]

    # def is_route_valid(self, route_id: str) -> bool:
    #   return 'routes.txt' in self.data and route_id in self.data['routes.txt']

    # def is_stop_valid(self, stop_id: str) -> bool:
    #   return 'stops.txt' in self.data and stop_id in self.data['stops.txt']

    # def get_stop_name(self, stop_id: str) -> str:
    #   """
    #   Get the name of a stop by its stop_id
    #   """
    #   if 'stops.txt' not in self.data:
    #     print('stops.txt not loaded')
    #     return stop_id

    #   if stop_id not in self.data['stops.txt']:
    #     print(f'stop_id {stop_id} not found in stops.txt')
    #     return stop_id

    #   return self.data['stops.txt'][stop_id]['stop_name']
