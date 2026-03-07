"""Fetch current weather for the Cariboo region.

Primary location: Horsefly Lake (home base).
Secondary: Williams Lake — included only when driving conditions are notable
(snow, freezing rain, fog, heavy rain, high winds).

Uses the Open-Meteo free API — no API key required.
Returns a plain-English summary suitable for injection into the podcast script prompt.
"""

import requests

# Horsefly Lake, BC (primary — home base)
HORSEFLY_LAT = 52.35
HORSEFLY_LON = -121.40

# Williams Lake, BC (secondary — driving/town conditions)
WILLIAMS_LAKE_LAT = 52.14
WILLIAMS_LAKE_LON = -122.14

TIMEZONE = "America/Vancouver"

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


def _fetch_location(lat, lon):
    """Fetch current conditions and daily forecast for a single location.

    Returns parsed dict or None on failure.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,weather_code,wind_speed_10m",
        "daily": ("temperature_2m_max,temperature_2m_min,"
                  "precipitation_sum,weather_code,wind_speed_10m_max"),
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
        current = data["current"]
        daily = data["daily"]

        return {
            "current_temp": round(current["temperature_2m"]),
            "current_code": current.get("weather_code", 0),
            "current_wind": round(current.get("wind_speed_10m", 0)),
            "high": round(daily["temperature_2m_max"][0]),
            "low": round(daily["temperature_2m_min"][0]),
            "precip": daily["precipitation_sum"][0],
            "daily_code": daily.get("weather_code", [0])[0],
            "max_wind": round(daily.get("wind_speed_10m_max", [0])[0]),
        }
    except (KeyError, IndexError, TypeError) as e:
        print(f"  Weather parsing failed ({lat}, {lon}): {e}")
        return None


def _has_driving_impact(loc):
    """Return True if conditions at this location affect driving."""
    if loc is None:
        return False
    return (
        loc["current_code"] in DRIVING_IMPACT_CODES
        or loc["daily_code"] in DRIVING_IMPACT_CODES
        or loc["max_wind"] > 50
    )


def _describe(code):
    """WMO code to human string."""
    return WMO_CODES.get(code, "mixed conditions")


def fetch_weather():
    """Fetch weather for Horsefly Lake (primary) and Williams Lake (driving).

    Returns:
        dict with 'summary' string for the script prompt, plus raw data
        None if the primary location fetch fails entirely
    """
    horsefly = _fetch_location(HORSEFLY_LAT, HORSEFLY_LON)
    williams = _fetch_location(WILLIAMS_LAKE_LAT, WILLIAMS_LAKE_LON)

    if not horsefly:
        return None

    hf = horsefly

    # Primary summary: Horsefly Lake conditions
    summary = (
        f"Out at Horsefly Lake it's {hf['current_temp']} degrees "
        f"with {_describe(hf['current_code'])}."
    )

    if hf["current_wind"] > 20:
        summary += f" Winds at {hf['current_wind']} k-p-h."

    summary += (
        f" Forecast high of {hf['high']}, low of {hf['low']} "
        f"with {_describe(hf['daily_code'])}."
    )

    if hf["precip"] > 0:
        summary += f" About {hf['precip']:.0f} millimetres of precipitation expected."

    # Seasonal advisories
    if hf["high"] > 30:
        summary += " It's a hot one — stay hydrated and check the BC Wildfire Dashboard."
    elif hf["low"] < -20:
        summary += " Bundle up out there — extreme cold advisory territory."
    elif hf["low"] < -10:
        summary += " Dress warm — proper Cariboo winter weather."

    # Williams Lake — only mention if driving conditions are notable
    if williams and _has_driving_impact(williams):
        wl = williams
        wl_conditions = _describe(wl["current_code"])
        wl_forecast = _describe(wl["daily_code"])

        # Pick whichever is more impactful for the driving note
        if wl["current_code"] in DRIVING_IMPACT_CODES:
            summary += (
                f" Heads up if you're heading into Williams Lake — "
                f"{wl_conditions} at {wl['current_temp']} degrees."
            )
        elif wl["daily_code"] in DRIVING_IMPACT_CODES:
            summary += (
                f" If you're driving into Williams Lake later, "
                f"watch for {wl_forecast}."
            )

        if wl["max_wind"] > 50:
            summary += f" Strong wind gusts up to {wl['max_wind']} k-p-h on the road."

        # Freezing rain / black ice warning
        if wl["current_code"] in {56, 57, 66, 67} or wl["daily_code"] in {56, 57, 66, 67}:
            summary += " Take it slow on the Horsefly Road."

    return {
        "horsefly": horsefly,
        "williams_lake": williams,
        "williams_lake_driving_impact": williams is not None and _has_driving_impact(williams),
        "summary": summary,
    }


def format_weather_for_prompt(weather_data):
    """Format weather data as a prompt section for script generation.

    Args:
        weather_data: dict from fetch_weather(), or None

    Returns:
        String to inject into the script generation prompt
    """
    if not weather_data:
        return ""

    return (
        "WEATHER CHECK (for the hosts to deliver naturally in the welcome section — "
        "keep it to 2-3 sentences, conversational, not a formal forecast. "
        "Horsefly Lake is home base; only mention Williams Lake if driving conditions "
        "are noted below):\n"
        f"{weather_data['summary']}\n\n"
    )
