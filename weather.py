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

Uses the Open-Meteo free API — no API key required. When Open-Meteo does not
answer for Horsefly, Environment Canada's city-page forecasts stand in
(`fetch_eccc_weather`): Williams Lake, 100 Mile House and Quesnel. There is no
Horsefly page there, so on those nights Williams Lake leads and Horsefly gets no
figure. The result's `source` names the provider, for the credits.
Returns a plain-English summary suitable for injection into the podcast script prompt.
"""

import random
import time
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

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

OPEN_METEO_SOURCE = "Open-Meteo"

# Environment Canada's city pages, the fallback when Open-Meteo turns us away. On
# 2026-11-20 and 11-21 every Open-Meteo call from GitHub's runner timed out; from
# another shared cloud address it answered 429 "Daily API request limit exceeded"
# (10,000 a day per IP, shared by every project on that address). Free and keyless,
# under the Open Government Licence - Canada, which asks for the source to be named.
ECCC_SOURCE = "Environment Canada"
ECCC_WRITTEN_CREDIT = "Environment and Climate Change Canada (Open Government Licence – Canada)"
ECCC_URL = "https://api.weather.gc.ca/collections/citypageweather-realtime/items/{site}"
# Williams Lake first: it leads the summary in Horsefly's place.
ECCC_SITES = (("Williams Lake", "bc-76"), ("100 Mile House", "bc-7"), ("Quesnel", "bc-64"))

# Open-Meteo is free, keyless and normally answers in well under a second, so a
# read timeout is a blip rather than a verdict — and there was no retry at all
# until 2026-09-06, when all five locations timed out inside the same 10 s
# window and the episode aired with no weather check at all. One retry costs
# nothing on a healthy night and is the difference between a blip and a
# missing segment; a genuine outage still gives up, and the caller reports it.
REQUEST_TIMEOUT_S = 10
FETCH_ATTEMPTS = 2
RETRY_BACKOFF_S = 2

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


def _get_json(url: str, params: dict, label: str) -> Any:
    """GET a JSON body, re-asking once on a transport failure; None when it never comes.

    Only a transport-level failure is worth re-asking: a timeout, a dropped
    connection or a 5xx. A malformed response would come back malformed a second
    time, and paying the backoff for it delays the remaining locations for nothing.
    """
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_S)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            retryable = isinstance(e, requests.exceptions.RequestException)
            if retryable and attempt < FETCH_ATTEMPTS:
                print(f"  Weather fetch failed ({label}), retrying: {e}")
                time.sleep(RETRY_BACKOFF_S)
                continue
            print(f"  Weather fetch failed ({label}): {e}")
            return None
    return None


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

    data = _get_json("https://api.open-meteo.com/v1/forecast", params, f"{lat}, {lon}")
    if data is None:
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
        dict with 'summary' string for the script prompt, raw data per location and
        'source' (the provider, for the credits). When Horsefly Lake's fetch fails,
        Environment Canada's forecast (`fetch_eccc_weather`), or None if that fails too.
    """
    horsefly = _fetch_location(HORSEFLY_LAT, HORSEFLY_LON)
    if not horsefly:
        # Open-Meteo is down or has turned this address away: the other four
        # would wait out the same timeouts, and without Horsefly nothing airs.
        return fetch_eccc_weather()
    hundred_mile = _fetch_location(HUNDRED_MILE_LAT, HUNDRED_MILE_LON)
    williams = _fetch_location(WILLIAMS_LAKE_LAT, WILLIAMS_LAKE_LON)
    quesnel = _fetch_location(QUESNEL_LAT, QUESNEL_LON)

    # Pick one Chilcotin plateau community at random
    chilcotin_town = random.choice(CHILCOTIN_TOWNS)
    chilcotin = _fetch_location(chilcotin_town["lat"], chilcotin_town["lon"])
    chilcotin_name = chilcotin_town["name"]

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
        "source": OPEN_METEO_SOURCE,
    }


def _en(node: Any) -> Any:
    """The English half of an Environment Canada {"en", "fr"} pair."""
    return node.get("en") if isinstance(node, dict) else None


def _eccc_temperature(period: dict, kind: str) -> int | None:
    for temp in (period.get("temperatures") or {}).get("temperature") or []:
        if _en(temp.get("class")) == kind and isinstance(_en(temp.get("value")), (int, float)):
            return round(_en(temp["value"]))
    return None


def _spoken(text: str) -> str:
    # The hosts' voices read "km/h" letter by letter; the Open-Meteo lines say k-p-h.
    return text.replace("km/h", "k-p-h")


def _eccc_location(props: dict, today: date) -> dict | None:
    """Today's forecast from one city page, or None when it has no forecast for today.

    The show is written between 1 and 3 AM, when the page's first period is still
    the night before, so today is found by its weekday, never as the first period.
    The morning figure is the MORNING_HOUR row of the hourly forecast.
    """
    try:
        periods = props["forecastGroup"]["forecasts"]
        weekday = today.strftime("%A")
        idx = next(i for i, f in enumerate(periods) if _en(f["period"]["value"]) == weekday)
        day = periods[idx]
        text = _en(day["textSummary"])
        high = _eccc_temperature(day, "high")
    except (KeyError, TypeError, StopIteration):
        return None
    if not text or high is None:
        return None
    low = None
    for j in (idx - 1, idx + 1):   # the night before today's day, else the night after
        if 0 <= j < len(periods):
            low = _eccc_temperature(periods[j], "low")
            if low is not None:
                break
    morning_temp = morning_conditions = None
    tz = ZoneInfo(TIMEZONE)
    for hour in (props.get("hourlyForecastGroup") or {}).get("hourlyForecasts") or []:
        try:
            at = datetime.fromisoformat(hour["timestamp"].replace("Z", "+00:00")).astimezone(tz)
        except (KeyError, TypeError, ValueError):
            continue
        if at.date() == today and at.hour == MORNING_HOUR:
            value = _en((hour.get("temperature") or {}).get("value"))
            morning_temp = round(value) if isinstance(value, (int, float)) else None
            morning_conditions = _en(hour.get("condition"))
            break
    warnings = [_en(w.get("description")) for w in props.get("warnings") or [] if isinstance(w, dict)]
    return {
        "text": _spoken(text),
        "conditions": _en((day.get("abbreviatedForecast") or {}).get("textSummary")) or "",
        "high": high,
        "low": low,
        "morning_temp": morning_temp,
        "morning_conditions": morning_conditions,
        "warnings": [w for w in warnings if isinstance(w, str) and w],
    }


def fetch_eccc_weather(today: date | None = None) -> dict | None:
    """Environment Canada's forecast for the Cariboo, or None if Williams Lake has none.

    Williams Lake leads in Horsefly's place (there is no Horsefly city page) and
    the summary says so, so no Horsefly figure is ever read on air.
    """
    today = today or datetime.now(ZoneInfo(TIMEZONE)).date()
    found: dict[str, dict | None] = {}
    for name, site in ECCC_SITES:
        data = _get_json(ECCC_URL.format(site=site), {"f": "json", "lang": "en"}, name)
        props = data.get("properties") if isinstance(data, dict) else None
        found[name] = _eccc_location(props, today) if isinstance(props, dict) else None
    wl = found["Williams Lake"]
    if not wl:
        return None

    lead = "Environment Canada's forecast for Williams Lake, the nearest to Horsefly"
    if wl["morning_temp"] is not None:
        lead += (f": around {wl['morning_temp']} degrees this morning"
                 + (f", {wl['morning_conditions'].lower()}" if wl["morning_conditions"] else ""))
    summary = f"{lead}. Today: {wl['text']}"
    regional = [f"{name} {loc['conditions'].lower() or 'forecast'}, high {loc['high']}"
                for name, loc in found.items() if loc and name != "Williams Lake"]
    if regional:
        summary += " Across the Cariboo — " + ", ".join(regional) + "."
    alerts = list(dict.fromkeys(w for loc in found.values() if loc for w in loc["warnings"]))
    for alert in alerts[:2]:
        summary += f" Environment Canada alert: {_spoken(alert)}"
    return {
        "williams_lake": wl,
        "hundred_mile": found.get("100 Mile House"),
        "quesnel": found.get("Quesnel"),
        "summary": summary,
        "source": ECCC_SOURCE,
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
    if d.get("source") == ECCC_SOURCE:
        rows = [{"name": name, "temp": loc["morning_temp"] if loc["morning_temp"] is not None else loc["high"],
                 "conditions": loc["morning_conditions"] or loc["conditions"],
                 "high": loc["high"], "low": "–" if loc["low"] is None else loc["low"]}
                for name, loc in (("Williams Lake", d.get("williams_lake")),
                                  ("100 Mile House", d.get("hundred_mile")),
                                  ("Quesnel", d.get("quesnel"))) if loc]
        return {"locations": rows, "source": ECCC_SOURCE} if rows else None

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
    return {"locations": locations, "source": OPEN_METEO_SOURCE}


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
    lead = ("home base at Horsefly Lake, then a brief sweep across each of the other "
            "communities named below")
    if weather_data.get("source") == ECCC_SOURCE:
        # SOURCED OR UNSAID: Environment Canada has no Horsefly forecast.
        lead = ("Williams Lake first, then a brief sweep across the other communities named "
                "below. Environment Canada has no Horsefly forecast today: give no figure for "
                "Horsefly and never present Williams Lake's as Horsefly's")
    return (
        "WEATHER CHECK (Cariboo-wide — for the hosts to deliver naturally in the welcome "
        f"section. Cover the regional highlights: {lead}. Name each town plainly — "
        "no regional labels (\"on the Chilcotin plateau\", \"in the south Cariboo\") and no "
        "explaining where it is. Keep it to 2-3 sentences, conversational, not a formal "
        "forecast. Flag any driving alerts if noted below.\n"
        "THIS IS TODAY'S FORECAST, NOT CURRENT CONDITIONS. The episode is written overnight "
        "and heard at breakfast, so never say \"right now\" or \"at the moment\", and never "
        "state a clock time or the hour the forecast is for. Say \"this morning\" and "
        "\"today\". Do not mention tomorrow or any other day):\n"
        f"{weather_data['summary']}\n\n"
    )
