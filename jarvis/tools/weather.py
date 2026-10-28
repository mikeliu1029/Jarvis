"""Weather from Open-Meteo (free, no API key). Location from settings or IP geolocation."""
from __future__ import annotations

import logging
import time

import requests

from ..config import get_settings
from ..memory import get_memory
from .registry import P, registry

log = logging.getLogger(__name__)

WMO = {0: "clear skies", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
       82: "violent showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms",
       96: "thunderstorms with hail", 99: "thunderstorms with heavy hail"}

US_STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
             "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
             "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
             "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
             "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
             "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
             "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
             "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
             "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
             "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
             "DC": "District of Columbia"}


def _geocode(query: str) -> dict:
    name, _, qualifier = query.partition(",")
    qualifier = qualifier.strip()
    qualifier = US_STATES.get(qualifier.upper(), qualifier)
    r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                     params={"name": name.strip(), "count": 10, "language": "en", "format": "json"}, timeout=8)
    r.raise_for_status()
    results = r.json().get("results") or []
    if not results:
        raise ValueError(f"couldn't find a place called '{query}'")
    pick = results[0]
    if qualifier:
        q = qualifier.lower()
        for res in results:
            if q in {str(res.get(k, "")).lower() for k in ("admin1", "country", "country_code")}:
                pick = res
                break
    label = ", ".join(x for x in (pick.get("name"), pick.get("admin1") or pick.get("country")) if x)
    return {"lat": pick["latitude"], "lon": pick["longitude"], "label": label,
            "country": (pick.get("country_code") or "").upper()}


def _ip_location() -> dict:
    mem = get_memory()
    cached = mem.get("ip_location")
    if cached and time.time() - cached.get("at", 0) < 12 * 3600:
        return cached
    loc = None
    try:
        d = requests.get("https://ipapi.co/json/", timeout=6).json()
        if d.get("latitude"):
            loc = {"lat": d["latitude"], "lon": d["longitude"], "label": f"{d.get('city')}, {d.get('region')}",
                   "country": d.get("country_code", "")}
    except Exception:
        pass
    if loc is None:
        d = requests.get("http://ip-api.com/json/", timeout=6).json()
        loc = {"lat": d["lat"], "lon": d["lon"], "label": f"{d.get('city')}, {d.get('regionName')}",
               "country": d.get("countryCode", "")}
    loc["at"] = time.time()
    mem.set("ip_location", loc)
    return loc


def resolve_location(name: str | None = None) -> dict:
    configured = str(get_settings().get("user.location", "auto") or "auto").strip()
    query = name or (configured if configured.lower() != "auto" else "")
    return _geocode(query) if query else _ip_location()


def _imperial(country: str) -> bool:
    units = str(get_settings().get("user.units", "auto")).lower()
    if units in ("imperial", "metric"):
        return units == "imperial"
    return country in ("US", "LR", "MM")


@registry.tool(
    "get_weather",
    "Current weather plus today's and tomorrow's forecast. Location is optional (defaults to the user's).",
    {"location": P("string", "City, e.g. 'London' or 'Austin, TX'", required=False)},
)
def get_weather(location: str = "") -> str:
    loc = resolve_location(location or None)
    imperial = _imperial(loc.get("country", ""))
    params = {
        "latitude": loc["lat"], "longitude": loc["lon"], "timezone": "auto", "forecast_days": 2,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "temperature_unit": "fahrenheit" if imperial else "celsius",
        "wind_speed_unit": "mph" if imperial else "kmh",
    }
    r = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=8)
    r.raise_for_status()
    d = r.json()
    c, daily = d["current"], d["daily"]
    deg, wind = ("°F", "mph") if imperial else ("°C", "km/h")

    def day(i: int) -> str:
        return (f"{WMO.get(daily['weather_code'][i], 'mixed conditions')}, high {round(daily['temperature_2m_max'][i])}{deg}, "
                f"low {round(daily['temperature_2m_min'][i])}{deg}, "
                f"{daily['precipitation_probability_max'][i] or 0}% chance of rain")

    return (f"Weather for {loc['label']}: {WMO.get(c['weather_code'], 'mixed conditions')}, "
            f"{round(c['temperature_2m'])}{deg} (feels like {round(c['apparent_temperature'])}{deg}), "
            f"humidity {c['relative_humidity_2m']}%, wind {round(c['wind_speed_10m'])} {wind}.\n"
            f"Today: {day(0)}.\nTomorrow: {day(1)}.")
