"""Fetch today's weather forecast for the Cariboo region.

The show is written between 1 and 3 AM Pacific and heard from 6:30 AM, so
"current conditions" describes the middle of the night nobody was awake for and
"tomorrow" is the day the listener is already living in. Everything here is
today's forecast: the MORNING_HOUR outlook in place of current conditions, plus
today's high, low and precipitation odds. The forecast hour is never stated on
air — the listener hears the morning they are getting up into.

Covers four communities spread across the Cariboo for a regional picture:
  - Horsefly Lake  (home base)
  - 100 Mile House (south Cariboo)
  - Williams Lake  (central Cariboo hub)
  - Quesnel        (north Cariboo)

Plus a randomly selected Chilcotin plateau community for regional variety:
  - Alexis Creek, Riske Creek, Hanceville, Tatla Lake, Anahim Lake, or Redstone

Uses the Open-Meteo free API — no API key required.
Returns a plain-English summary suitable for injection into the podcast script prompt.
"""

import random
import requests

# Horsefly Lake, BC (home base)
HORSEFLY_LAT = 52.35
HORSEFLY_LON = -121.40

# 100 Mile House, BC (south Cariboo)
HUNDRED_MILE_LAT = 51.64
HUNDRED_MILE_LON = -121.43

# Williams Lake, BC (central Cariboo hub / driving waypoint)
WILLIAMS_LAKE_LAT = 52.14
WILLIAMS_LAKE_LON = -122.14

# Quesnel, BC (north Cariboo)
QUESNEL_LAT = 53.02
QUESNEL_LON = -122.49

# Chilcotin plateau communities (one selected at random each episode)
CHILCOTIN_TOWNS = [
    {"name": "Alexis Creek",  "lat": 52.08, "lon": -123.27},
    {"name": "Riske Creek",   "lat": 51.97, "lon": -122.53},
    {"name": "Hanceville",    "lat": 51.90, "lon": -123.07},
    {"name": "Tatla Lake",    "lat": 51.99, "lon": -124.42},
    {"name": "Anahim Lake",   "lat": 52.46, "lon": -125.31},
    {"name": "Redstone",      "lat": 51.82, "lon": -123.70},
]

TIMEZONE = "America/Vancouver"

# Local hour the forecast is read for — the listener's morning, not the 1 AM
# render time. Never spoken on air.
MORNING_HOUR = 8

# WMO weather interpretation codes -> human-readable descriptions
# https://open-meteo.com/en/docs
WMO_CODES = {
    0: "clear skies",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "moderate drizzle",
    55: "dense drizzle",
    56: "light freezing drizzle",
    57: "dense freezing drizzle",
    61: "slight rain",
    63: "moderate rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "heavy freezing rain",
    71: "slight snowfall",
    73: "moderate snowfall",
    75: "heavy snowfall",
    77: "snow grains",
    80: "slight rain showers",
    81: "moderate rain showers",
    82: "violent rain showers",
    85: "slight snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with slight hail",
    99: "thunderstorm with heavy hail",
}

# WMO codes that affect driving between Horsefly and Williams Lake
DRIVING_IMPACT_CODES = {
    45, 48,         # fog / rime fog
    56, 57,         # freezing drizzle
    63, 65,         # moderate/heavy rain
    66, 67,         # freezing rain
    71, 73, 75, 77, # snow
    82,             # violent rain showers
    85, 86,         # snow showers
    95, 96, 99,     # thunderstorms
}


def _morning_index(hourly: dict, day: str) -> int:
    """Index of MORNING_HOUR on `day` in Open-Meteo's local-time hourly array."""
    return hourly["time"].index(f"{day}T{MORNING_HOUR:02d}:00")


def _fetch_location(lat, lon):
    """Fetch today's morning outlook and daily forecast for a single location.

    Returns parsed dict or None on failure.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,weather_code,wind_speed_10m",
        "daily": ("temperature_2m_max,temperature_2m_min,precipitation_sum,"
                  "precipitation_probability_max,weather_code,wind_speed_10m_max"),
        "timezone": TIMEZONE,
        "forecast_days": 1,
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
    }

    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  Weather fetch failed ({lat}, {lon}): {e}")
        return None

    try:
        daily = data["daily"]
        hourly = data["hourly"]
        # The morning row is located by today's own date, so a run that starts
        # before midnight and finishes after it cannot read yesterday's 8 AM.
        m = _morning_index(hourly, daily["time"][0])

        return {
            "morning_temp": round(hourly["temperature_2m"][m]),
            "morning_code": hourly["weather_code"][m],
            "morning_wind": round(hourly["wind_speed_10m"][m]),
            "high": round(daily["temperature_2m_max"][0]),
            "low": round(daily["temperature_2m_min"][0]),
            "precip": daily["precipitation_sum"][0],
            "precip_chance": daily.get("precipitation_probability_max", [None])[0],
            "daily_code": daily.get("weather_code", [0])[0],
            "max_wind": round(daily.get("wind_speed_10m_max", [0])[0]),
        }
    except (KeyError, IndexError, TypeError, ValueError) as e:
        print(f"  Weather parsing failed ({lat}, {lon}): {e}")
        return None


def _has_driving_impact(loc):
    """Return True if conditions at this location affect driving."""
    if loc is None:
        return False
    return (
        loc["morning_code"] in DRIVING_IMPACT_CODES
        or loc["daily_code"] in DRIVING_IMPACT_CODES
        or loc["max_wind"] > 50
    )


def _describe(code):
    """WMO code to human string."""
    return WMO_CODES.get(code, "mixed conditions")


def _precip_phrase(loc: dict) -> str:
    """Today's precipitation odds and amount, as a spoken clause (may be empty).

    The odds are the point of it — an amount alone reads as a certainty, and a
    dry-but-possible day says nothing at all without them.
    """
    chance = loc.get("precip_chance")
    amount = loc.get("precip") or 0

    if chance:
        phrase = f" {chance} percent chance of precipitation"
        return phrase + (f", about {amount:.0f} millimetres." if amount > 0 else ".")
    if amount > 0:
        return f" About {amount:.0f} millimetres of precipitation."
    if chance == 0:
        return " No precipitation expected."
    return ""


def fetch_weather():
    """Fetch weather for Cariboo communities and return a regional summary.

    Locations: Horsefly Lake (home), 100 Mile House, Williams Lake, Quesnel,
    plus a randomly selected Chilcotin plateau community for variety.

    Returns:
        dict with 'summary' string for the script prompt, plus raw data per location.
        None if the primary (Horsefly Lake) fetch fails entirely.
    """
    horsefly = _fetch_location(HORSEFLY_LAT, HORSEFLY_LON)
    hundred_mile = _fetch_location(HUNDRED_MILE_LAT, HUNDRED_MILE_LON)
    williams = _fetch_location(WILLIAMS_LAKE_LAT, WILLIAMS_LAKE_LON)
    quesnel = _fetch_location(QUESNEL_LAT, QUESNEL_LON)

    # Pick one Chilcotin plateau community at random
    chilcotin_town = random.choice(CHILCOTIN_TOWNS)
    chilcotin = _fetch_location(chilcotin_town["lat"], chilcotin_town["lon"])
    chilcotin_name = chilcotin_town["name"]

    if not horsefly:
        return None

    hf = horsefly

    # Home-base lead: Horsefly Lake's morning outlook, then today's numbers.
    summary = (
        f"Out at Horsefly Lake this morning, {_describe(hf['morning_code'])} "
        f"and around {hf['morning_temp']} degrees."
    )

    if hf["morning_wind"] > 20:
        summary += f" Morning winds around {hf['morning_wind']} k-p-h."

    summary += f" Today's high {hf['high']}, low {hf['low']}."

    # The day's own code, only when it says something the morning line did not.
    if hf["daily_code"] != hf["morning_code"]:
        summary += f" Through the day, {_describe(hf['daily_code'])}."

    summary += _precip_phrase(hf)

    # Regional snapshot: brief conditions for each Cariboo community,
    # including the random Chilcotin plateau pick
    regional_parts = []
    for loc_name, loc in [
        ("100 Mile House", hundred_mile),
        ("Williams Lake", williams),
        ("Quesnel", quesnel),
        (chilcotin_name, chilcotin),
    ]:
        if loc:
            regional_parts.append(
                f"{loc_name} around {loc['morning_temp']} with {_describe(loc['morning_code'])}"
            )

    if regional_parts:
        summary += " Across the Cariboo — " + ", ".join(regional_parts) + "."

    # Seasonal advisories — based on the coldest/hottest spot in the region
    all_locs = [l for l in [horsefly, hundred_mile, williams, quesnel, chilcotin] if l]
    max_high = max(l["high"] for l in all_locs)
    min_low = min(l["low"] for l in all_locs)

    if max_high > 30:
        summary += " It's a hot one across the region — stay hydrated and check the BC Wildfire Dashboard."
    elif min_low < -20:
        summary += " Bundle up — extreme cold conditions in parts of the Cariboo."
    elif min_low < -10:
        summary += " Dress warm — proper Cariboo winter weather across the region."

    # Driving alert: Williams Lake route conditions
    if williams and _has_driving_impact(williams):
        wl = williams
        wl_conditions = _describe(wl["morning_code"])
        wl_forecast = _describe(wl["daily_code"])

        if wl["morning_code"] in DRIVING_IMPACT_CODES:
            summary += (
                f" Heads up if you're heading into Williams Lake this morning — "
                f"{wl_conditions} at {wl['morning_temp']} degrees."
            )
        elif wl["daily_code"] in DRIVING_IMPACT_CODES:
            summary += (
                f" If you're driving into Williams Lake later, "
                f"watch for {wl_forecast}."
            )

        if wl["max_wind"] > 50:
            summary += f" Strong wind gusts up to {wl['max_wind']} k-p-h on the road."

        if wl["morning_code"] in {56, 57, 66, 67} or wl["daily_code"] in {56, 57, 66, 67}:
            summary += " Take it slow on the Horsefly Road."

    return {
        "horsefly": horsefly,
        "hundred_mile": hundred_mile,
        "williams_lake": williams,
        "quesnel": quesnel,
        "chilcotin_town": chilcotin,
        "chilcotin_town_name": chilcotin_name,
        "williams_lake_driving_impact": williams is not None and _has_driving_impact(williams),
        "summary": summary,
    }


def weather_slide_data(weather_data: dict | None) -> dict | None:
    """Compact per-location summary for the video weather slide, or None.

    Args:
        weather_data: dict from fetch_weather(), or None

    Returns:
        {"locations": [{"name", "temp", "conditions", "high", "low"}, ...],
         "source": "Open-Meteo"} — or None if no weather data.
    """
    if not weather_data:
        return None

    d = weather_data
    locations = []
    for name, loc in [
        ("Horsefly Lake", d.get("horsefly")),
        ("100 Mile House", d.get("hundred_mile")),
        ("Williams Lake", d.get("williams_lake")),
        ("Quesnel", d.get("quesnel")),
        (d.get("chilcotin_town_name"), d.get("chilcotin_town")),
    ]:
        if not name or not loc:
            continue
        locations.append({
            "name": name,
            "temp": loc["morning_temp"],
            "conditions": _describe(loc["morning_code"]),
            "high": loc["high"],
            "low": loc["low"],
        })

    if not locations:
        return None
    return {"locations": locations, "source": "Open-Meteo"}


def format_weather_for_prompt(weather_data):
    """Format weather data as a prompt section for script generation.

    Args:
        weather_data: dict from fetch_weather(), or None

    Returns:
        String to inject into the script generation prompt
    """
    if not weather_data:
        return ""

    # The rotating fifth town is named and nothing more. Calling it "today's
    # Chilcotin plateau community" in the prompt is how that label reached the
    # air; the listener wants the forecast for the town, not its geography.
    return (
        "WEATHER CHECK (Cariboo-wide — for the hosts to deliver naturally in the welcome "
        "section. Cover the regional highlights: home base at Horsefly Lake, then a brief "
        "sweep across each of the other communities named below. Name each town plainly — "
        "no regional labels (\"on the Chilcotin plateau\", \"in the south Cariboo\") and no "
        "explaining where it is. Keep it to 2-3 sentences, conversational, not a formal "
        "forecast. Flag any driving alerts if noted below.\n"
        "THIS IS TODAY'S FORECAST, NOT CURRENT CONDITIONS. The episode is written overnight "
        "and heard at breakfast, so never say \"right now\" or \"at the moment\", and never "
        "state a clock time or the hour the forecast is for. Say \"this morning\" and "
        "\"today\". Do not mention tomorrow or any other day):\n"
        f"{weather_data['summary']}\n\n"
    )
