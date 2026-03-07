"""Fetch current weather and forecast for the Cariboo region (Williams Lake, BC).

Uses the Open-Meteo free API — no API key required.
Returns a plain-English summary suitable for injection into the podcast script prompt.
"""

import requests

# Williams Lake, BC coordinates
WILLIAMS_LAKE_LAT = 52.14
WILLIAMS_LAKE_LON = -122.14
TIMEZONE = "America/Vancouver"

# WMO weather interpretation codes → human-readable descriptions
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


def fetch_weather():
    """Fetch current conditions and today's forecast for Williams Lake.

    Returns:
        dict with keys: current_temp, current_conditions, high, low,
              precipitation_mm, wind_kph, summary (formatted string)
        None if the API call fails
    """
    params = {
        "latitude": WILLIAMS_LAKE_LAT,
        "longitude": WILLIAMS_LAKE_LON,
        "current": "temperature_2m,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code,wind_speed_10m_max",
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
        print(f"  Weather fetch failed: {e}")
        return None

    try:
        current = data["current"]
        daily = data["daily"]

        current_temp = round(current["temperature_2m"])
        current_code = current.get("weather_code", 0)
        current_wind = round(current.get("wind_speed_10m", 0))

        high = round(daily["temperature_2m_max"][0])
        low = round(daily["temperature_2m_min"][0])
        precip = daily["precipitation_sum"][0]
        daily_code = daily.get("weather_code", [0])[0]
        max_wind = round(daily.get("wind_speed_10m_max", [0])[0])

        current_conditions = WMO_CODES.get(current_code, "unknown conditions")
        forecast_conditions = WMO_CODES.get(daily_code, "mixed conditions")

        # Build a natural-language summary for the hosts to read
        summary = (
            f"Right now in Williams Lake it's {current_temp} degrees Celsius "
            f"with {current_conditions}."
        )

        if current_wind > 20:
            summary += f" Winds are gusting to {current_wind} kilometres per hour."

        summary += (
            f" Today's forecast calls for a high of {high} "
            f"and a low of {low} with {forecast_conditions}."
        )

        if precip > 0:
            summary += f" Expecting about {precip:.0f} millimetres of precipitation."

        if max_wind > 40:
            summary += f" Wind gusts up to {max_wind} k-p-h."

        # Add seasonal context for wildfire or winter conditions
        if high > 30:
            summary += " It's a hot one — stay hydrated and check the BC Wildfire Dashboard."
        elif low < -20:
            summary += " Bundle up out there — extreme cold advisory territory."
        elif low < -10:
            summary += " Dress warm — it's proper Cariboo winter weather."

        return {
            "current_temp": current_temp,
            "current_conditions": current_conditions,
            "high": high,
            "low": low,
            "precipitation_mm": precip,
            "wind_kph": current_wind,
            "max_wind_kph": max_wind,
            "forecast_conditions": forecast_conditions,
            "summary": summary,
        }

    except (KeyError, IndexError, TypeError) as e:
        print(f"  Weather parsing failed: {e}")
        return None


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
        "keep it to 2-3 sentences, conversational, not a formal forecast):\n"
        f"{weather_data['summary']}\n\n"
    )
