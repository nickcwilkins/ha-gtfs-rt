from enum import Enum
import logging
import aiohttp
import asyncio
from google.transit import gtfs_realtime_pb2


class RouteType(Enum):
    """GTFS Route Types"""

    TRAM = 0
    SUBWAY = 1
    RAIL = 2
    BUS = 3
    FERRY = 4
    CABLE_TRAM = 5
    AERIAL_LIFT = 6
    FUNICULAR = 7
    TROLLEYBUS = 11
    MONORAIL = 12


_LOGGER = logging.getLogger(__name__)


async def async_get_static_feed(url: str, headers: dict) -> bytes:
    """Returns"""
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url) as response:
            return await response.read()


async def _async_get_realtime_data(url: str, session: aiohttp.ClientSession) -> bytes:
    """Returns the text of the response from the GTFS RT api endpoint."""
    async with session.get(url) as response:
        return await response.read()


async def async_get_feed(
    route_update_url: str,
    vehicle_position_url: str | None,
    alerts_url: str | None,
    headers: dict,
) -> gtfs_realtime_pb2.FeedMessage:
    """Get a feed message that is the result of merging responses from from each endpoint."""
    async with aiohttp.ClientSession(headers=headers) as session:
        tasks = []
        if route_update_url:
            tasks.append(_async_get_realtime_data(route_update_url, session))
        if vehicle_position_url:
            tasks.append(_async_get_realtime_data(vehicle_position_url, session))
        if alerts_url:
            tasks.append(_async_get_realtime_data(alerts_url, session))

        responses = await asyncio.gather(*tasks)
        feed = gtfs_realtime_pb2.FeedMessage()
        if len(responses) > 0:
            for response in responses:
                feed.MergeFromString(response)

        return feed
