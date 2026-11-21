"""Tests for the weather module."""

from unittest.mock import patch, MagicMock, call

from weather import (
    fetch_weather, format_weather_for_prompt, weather_slide_data, WMO_CODES,
    DRIVING_IMPACT_CODES, _has_driving_impact,
)


DAY = "2026-09-01"


def _mock_api_response(morning_temp=5, morning_code=2, wind=10,
                       high=8, low=-3, precip=0, precip_chance=0,
                       daily_code=3, max_wind=15):
    """Build a mock Open-Meteo JSON response for one location (today only)."""
    hours = [f"{DAY}T{h:02d}:00" for h in range(24)]
    return {
        "hourly": {
            "time": hours,
            # Only the 08:00 row carries the values under test; every other hour
            # is deliberately wrong, so reading the wrong hour fails loudly.
            "temperature_2m": [morning_temp if h == 8 else -99 for h in range(24)],
            "weather_code": [morning_code if h == 8 else 95 for h in range(24)],
            "wind_speed_10m": [wind if h == 8 else 999 for h in range(24)],
        },
        "daily": {
            "time": [DAY],
            "temperature_2m_max": [high],
            "temperature_2m_min": [low],
            "precipitation_sum": [precip],
            "precipitation_probability_max": [precip_chance],
            "weather_code": [daily_code],
            "wind_speed_10m_max": [max_wind],
        },
    }


def _make_mock_get(*responses):
    """Return a mock requests.get that returns different responses per call."""
    mock_resps = []
    for resp_data in responses:
        m = MagicMock()
        m.json.return_value = resp_data
        m.raise_for_status = MagicMock()
        mock_resps.append(m)
    mock_get = MagicMock(side_effect=mock_resps)
    return mock_get


class TestFetchWeather:
    @patch("weather.requests.get")
    def test_returns_horsefly_primary(self, mock_get):
        """Horsefly Lake is the primary location in the summary."""
        horsefly = _mock_api_response(morning_temp=-8, morning_code=1)
        williams = _mock_api_response(morning_temp=-5, morning_code=2)
        mock_get.side_effect = [
            MagicMock(json=MagicMock(return_value=horsefly), raise_for_status=MagicMock()),
            MagicMock(json=MagicMock(return_value=williams), raise_for_status=MagicMock()),
        ]

        result = fetch_weather()
        assert result is not None
        assert "Horsefly Lake" in result["summary"]
        # Williams Lake should NOT appear (no driving impact with code 2)
        assert "Williams Lake" not in result["summary"]
        assert result["horsefly"]["morning_temp"] == -8

    @patch("weather.requests.get")
    def test_williams_lake_included_on_snow(self, mock_get):
        """Williams Lake appears when there's snowfall (driving impact)."""
        horsefly = _mock_api_response(morning_temp=-5, morning_code=3)
        hundred_mile = _mock_api_response(morning_temp=-4, morning_code=2)
        williams = _mock_api_response(morning_temp=-3, morning_code=73)  # moderate snowfall
        quesnel = _mock_api_response(morning_temp=-6, morning_code=1)
        mock_get.side_effect = [
            MagicMock(json=MagicMock(return_value=horsefly), raise_for_status=MagicMock()),
            MagicMock(json=MagicMock(return_value=hundred_mile), raise_for_status=MagicMock()),
            MagicMock(json=MagicMock(return_value=williams), raise_for_status=MagicMock()),
            MagicMock(json=MagicMock(return_value=quesnel), raise_for_status=MagicMock()),
        ]

        result = fetch_weather()
        assert "Williams Lake" in result["summary"]
        assert result["williams_lake_driving_impact"] is True

    @patch("weather.requests.get")
    def test_williams_lake_included_on_freezing_rain(self, mock_get):
        """Freezing rain triggers driving warning and slow-down advice."""
        horsefly = _mock_api_response(morning_temp=-2, morning_code=2)
        hundred_mile = _mock_api_response(morning_temp=-1, morning_code=2)
        williams = _mock_api_response(morning_temp=-1, morning_code=66)  # freezing rain
        quesnel = _mock_api_response(morning_temp=-3, morning_code=1)
        mock_get.side_effect = [
            MagicMock(json=MagicMock(return_value=horsefly), raise_for_status=MagicMock()),
            MagicMock(json=MagicMock(return_value=hundred_mile), raise_for_status=MagicMock()),
            MagicMock(json=MagicMock(return_value=williams), raise_for_status=MagicMock()),
            MagicMock(json=MagicMock(return_value=quesnel), raise_for_status=MagicMock()),
        ]

        result = fetch_weather()
        assert "Williams Lake" in result["summary"]
        assert "Horsefly Road" in result["summary"]

    @patch("weather.requests.get")
    def test_williams_lake_included_on_high_winds(self, mock_get):
        """High winds at Williams Lake trigger driving warning."""
        horsefly = _mock_api_response(morning_temp=10, morning_code=2)
        hundred_mile = _mock_api_response(morning_temp=11, morning_code=2)
        williams = _mock_api_response(morning_temp=12, morning_code=2, max_wind=65)
        quesnel = _mock_api_response(morning_temp=9, morning_code=1)
        mock_get.side_effect = [
            MagicMock(json=MagicMock(return_value=horsefly), raise_for_status=MagicMock()),
            MagicMock(json=MagicMock(return_value=hundred_mile), raise_for_status=MagicMock()),
            MagicMock(json=MagicMock(return_value=williams), raise_for_status=MagicMock()),
            MagicMock(json=MagicMock(return_value=quesnel), raise_for_status=MagicMock()),
        ]

        result = fetch_weather()
        assert "Williams Lake" in result["summary"] or "wind" in result["summary"].lower()

    @patch("weather.requests.get")
    def test_includes_precipitation(self, mock_get):
        horsefly = _mock_api_response(precip=12.5, precip_chance=80)
        williams = _mock_api_response()
        mock_get.side_effect = [
            MagicMock(json=MagicMock(return_value=horsefly), raise_for_status=MagicMock()),
            MagicMock(json=MagicMock(return_value=williams), raise_for_status=MagicMock()),
        ]

        result = fetch_weather()
        assert "80 percent chance of precipitation" in result["summary"]
        assert "millimetres" in result["summary"]

    @patch("weather.requests.get")
    def test_precip_chance_without_accumulation(self, mock_get):
        """Odds are worth saying on a day that may stay dry."""
        horsefly = _mock_api_response(precip=0, precip_chance=30)
        mock_get.side_effect = [
            MagicMock(json=MagicMock(return_value=horsefly), raise_for_status=MagicMock()),
        ]

        result = fetch_weather()
        assert "30 percent chance of precipitation." in result["summary"]
        assert "millimetres" not in result["summary"]

    @patch("weather.requests.get")
    def test_reads_the_morning_hour_not_current_conditions(self, mock_get):
        """Temperature and conditions come from today's 08:00 forecast row."""
        horsefly = _mock_api_response(morning_temp=3, morning_code=0, wind=25)
        mock_get.side_effect = [
            MagicMock(json=MagicMock(return_value=horsefly), raise_for_status=MagicMock()),
        ]

        result = fetch_weather()
        hf = result["horsefly"]
        assert (hf["morning_temp"], hf["morning_code"], hf["morning_wind"]) == (3, 0, 25)
        assert "around 3 degrees" in result["summary"]
        assert "clear skies" in result["summary"]
        assert "25 k-p-h" in result["summary"]

    @patch("weather.requests.get")
    def test_summary_is_today_only(self, mock_get):
        """No tomorrow, no clock time, no present-tense current conditions."""
        horsefly = _mock_api_response(high=21, low=6)
        mock_get.side_effect = [
            MagicMock(json=MagicMock(return_value=horsefly), raise_for_status=MagicMock()),
        ]

        summary = fetch_weather()["summary"]
        assert "Tomorrow" not in summary
        assert "8:00" not in summary and "a.m." not in summary
        assert "Today's high 21, low 6." in summary

    @patch("weather.requests.get")
    def test_missing_morning_row_fails_the_location(self, mock_get):
        """A response without today's 08:00 row is a parse failure, not a guess."""
        broken = _mock_api_response()
        broken["hourly"]["time"] = [f"2026-08-31T{h:02d}:00" for h in range(24)]
        mock_get.side_effect = [
            MagicMock(json=MagicMock(return_value=broken), raise_for_status=MagicMock()),
        ]

        assert fetch_weather() is None

    @patch("weather.requests.get")
    def test_requests_only_today(self, mock_get):
        """One forecast day — tomorrow is never fetched, so it cannot be aired."""
        mock_get.side_effect = [
            MagicMock(json=MagicMock(return_value=_mock_api_response()),
                      raise_for_status=MagicMock()),
        ]

        fetch_weather()
        params = mock_get.call_args_list[0].kwargs["params"]
        assert params["forecast_days"] == 1
        assert "precipitation_probability_max" in params["daily"]
        assert "current" not in params

    @patch("weather.requests.get")
    def test_cold_warning(self, mock_get):
        horsefly = _mock_api_response(low=-25, morning_temp=-20)
        williams = _mock_api_response(low=-22, morning_temp=-18)
        mock_get.side_effect = [
            MagicMock(json=MagicMock(return_value=horsefly), raise_for_status=MagicMock()),
            MagicMock(json=MagicMock(return_value=williams), raise_for_status=MagicMock()),
        ]

        result = fetch_weather()
        assert "extreme cold" in result["summary"].lower() or "bundle up" in result["summary"].lower()

    @patch("weather.requests.get")
    def test_returns_none_on_horsefly_failure(self, mock_get):
        """If Horsefly fetch fails, return None entirely."""
        mock_get.side_effect = Exception("network error")
        result = fetch_weather()
        assert result is None

    @patch("weather.requests.get")
    def test_works_without_williams_lake(self, mock_get):
        """If Williams Lake fails but Horsefly succeeds, still returns data."""
        horsefly = _mock_api_response(morning_temp=5)
        horsefly_resp = MagicMock(json=MagicMock(return_value=horsefly), raise_for_status=MagicMock())
        williams_resp = MagicMock()
        williams_resp.raise_for_status.side_effect = Exception("timeout")

        mock_get.side_effect = [horsefly_resp, williams_resp]

        result = fetch_weather()
        assert result is not None
        assert "Horsefly Lake" in result["summary"]
        assert result["williams_lake"] is None


class TestHasDrivingImpact:
    def test_snow_is_driving_impact(self):
        loc = {"morning_code": 73, "daily_code": 2, "max_wind": 10}
        assert _has_driving_impact(loc) is True

    def test_clear_skies_no_impact(self):
        loc = {"morning_code": 0, "daily_code": 1, "max_wind": 15}
        assert _has_driving_impact(loc) is False

    def test_high_wind_is_impact(self):
        loc = {"morning_code": 0, "daily_code": 0, "max_wind": 55}
        assert _has_driving_impact(loc) is True

    def test_none_returns_false(self):
        assert _has_driving_impact(None) is False


class TestFormatWeatherForPrompt:
    def test_returns_empty_string_when_none(self):
        assert format_weather_for_prompt(None) == ""

    def test_returns_weather_check_block(self):
        weather = {
            "summary": "Out at Horsefly Lake it's 5 degrees with mainly clear.",
        }
        result = format_weather_for_prompt(weather)
        assert "WEATHER CHECK" in result
        assert "Horsefly Lake" in result

    def test_prompt_does_not_label_the_rotating_town(self):
        """The fifth town is named by the summary, never framed by its region."""
        result = format_weather_for_prompt({
            "summary": "Across the Cariboo — Tatla Lake around 4 with overcast.",
            "chilcotin_town_name": "Tatla Lake",
        })
        assert "Today's Chilcotin plateau spot" not in result
        assert "Chilcotin plateau community" not in result
        assert "no regional labels" in result

    def test_prompt_forbids_current_conditions_framing(self):
        result = format_weather_for_prompt({"summary": "Out at Horsefly Lake this morning."})
        assert "TODAY'S FORECAST, NOT CURRENT CONDITIONS" in result
        assert "clock time" in result

    def test_prompt_mentions_driving_context(self):
        weather = {
            "summary": "Out at Horsefly Lake it's -3 degrees. Williams Lake has snow.",
        }
        result = format_weather_for_prompt(weather)
        assert "driving" in result.lower()


class TestWMOCodes:
    def test_common_codes_have_descriptions(self):
        for code in [0, 1, 2, 3, 61, 71, 95]:
            assert code in WMO_CODES

    def test_all_driving_impact_codes_exist_in_wmo(self):
        for code in DRIVING_IMPACT_CODES:
            assert code in WMO_CODES, f"Driving impact code {code} not in WMO_CODES"


def _slide_loc(temp=15, code=2):
    return {
        "morning_temp": temp, "morning_code": code, "morning_wind": 5,
        "high": temp + 5, "low": temp - 8, "precip": 0, "precip_chance": 0,
        "daily_code": 1, "max_wind": 10,
    }


class TestWeatherSlideData:
    def test_none_input_returns_none(self):
        assert weather_slide_data(None) is None

    def test_builds_locations_and_source(self):
        data = weather_slide_data({
            "horsefly": _slide_loc(15), "hundred_mile": _slide_loc(14),
            "williams_lake": _slide_loc(17), "quesnel": _slide_loc(16),
            "chilcotin_town": _slide_loc(10), "chilcotin_town_name": "Tatla Lake",
            "summary": "unused",
        })
        assert data["source"] == "Open-Meteo"
        names = [loc["name"] for loc in data["locations"]]
        assert names == ["Horsefly Lake", "100 Mile House", "Williams Lake",
                         "Quesnel", "Tatla Lake"]
        hf = data["locations"][0]
        assert hf == {"name": "Horsefly Lake", "temp": 15,
                      "conditions": WMO_CODES[2], "high": 20, "low": 7}

    def test_failed_location_skipped(self):
        data = weather_slide_data({
            "horsefly": _slide_loc(15), "hundred_mile": None,
            "williams_lake": None, "quesnel": None,
            "chilcotin_town": None, "chilcotin_town_name": "Nemiah Valley",
            "summary": "unused",
        })
        assert [loc["name"] for loc in data["locations"]] == ["Horsefly Lake"]

    def test_all_locations_failed_returns_none(self):
        assert weather_slide_data({
            "horsefly": None, "hundred_mile": None, "williams_lake": None,
            "quesnel": None, "chilcotin_town": None, "chilcotin_town_name": "",
            "summary": "",
        }) is None


class TestTransientFailureRetry:
    """2026-09-06: all five Open-Meteo calls hit their 10 s read timeout inside
    the same window, `fetch_weather` returned None, and the episode aired with no
    weather check. There was no retry at all, and the caller reported nothing —
    the listener noticed the missing segment before the operator did."""

    @patch("weather.time.sleep")
    @patch("weather.requests.get")
    def test_a_timeout_is_re_asked_before_giving_up(self, mock_get, _sleep):
        import requests as _requests
        ok = MagicMock()
        ok.json.return_value = _mock_api_response()
        ok.raise_for_status = MagicMock()
        # Every location times out once, then answers.
        mock_get.side_effect = [
            _requests.exceptions.ReadTimeout("read timeout=10"), ok,
        ] * 5

        result = fetch_weather()
        assert result is not None
        assert mock_get.call_count == 10

    @patch("weather.time.sleep")
    @patch("weather.requests.get")
    def test_a_persistent_outage_still_gives_up(self, mock_get, _sleep):
        import requests as _requests
        mock_get.side_effect = _requests.exceptions.ReadTimeout("read timeout=10")
        assert fetch_weather() is None

    @patch("weather.time.sleep")
    @patch("weather.requests.get")
    def test_a_malformed_response_is_not_re_asked(self, mock_get, sleep):
        """It would come back malformed a second time, and the backoff delays
        the four remaining locations for nothing."""
        bad = MagicMock()
        bad.json.return_value = {"unexpected": True}
        bad.raise_for_status = MagicMock()
        mock_get.return_value = bad

        assert fetch_weather() is None
        # One call for Horsefly, no retries; without it the other four Open-Meteo
        # calls are not made, and Environment Canada's three pages are asked once each.
        assert mock_get.call_count == 4
        sleep.assert_not_called()


# --- Environment Canada fallback (2026-11-21) ----------------------------------
# Open-Meteo's per-address daily cap turned GitHub's runner away two nights running
# (every call timed out; from another cloud address it answered 429 "Daily API
# request limit exceeded"). Environment Canada's city pages stand in.

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import weather


def _city_page(today, high=0, low=-8, morning=-4, warnings=None):
    """One api.weather.gc.ca city page as the 1 AM run sees it: last night's period first."""
    en = lambda v: {"en": v, "fr": v}
    weekday = today.strftime("%A")
    yesterday = (today - timedelta(days=1)).strftime("%A")
    eight_am = datetime(today.year, today.month, today.day, 8,
                        tzinfo=ZoneInfo("America/Vancouver")).astimezone(timezone.utc)
    hour = lambda at, temp, cond: {"timestamp": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   "temperature": {"value": en(temp)}, "condition": en(cond)}
    return {"properties": {
        "forecastGroup": {"forecasts": [
            {"period": {"textForecastName": en("Tonight"), "value": en(f"{yesterday} night")},
             "temperatures": {"temperature": [{"class": en("low"), "value": en(low)}]},
             "textSummary": en("Clear. Low minus 8."),
             "abbreviatedForecast": {"textSummary": en("Clear")}},
            {"period": {"textForecastName": en(weekday), "value": en(weekday)},
             "temperatures": {"temperature": [{"class": en("high"), "value": en(high)}]},
             "textSummary": en("Cloudy. 30 percent chance of flurries. Wind up to 15 km/h. High zero."),
             "abbreviatedForecast": {"textSummary": en("Chance of flurries")}},
        ]},
        "hourlyForecastGroup": {"hourlyForecasts": [
            hour(eight_am - timedelta(hours=1), -99, "Thunderstorm"),  # the wrong hour, loudly
            hour(eight_am, morning, "Chance of flurries"),
        ]},
        "warnings": warnings or [],
    }}


def _pages(*bodies):
    resps = []
    for body in bodies:
        m = MagicMock()
        m.json.return_value = body
        m.raise_for_status = MagicMock()
        resps.append(m)
    return resps


SATURDAY = date(2026, 11, 21)


class TestEnvironmentCanadaFallback:
    @patch("weather.requests.get")
    def test_reads_today_by_weekday_and_the_morning_hour(self, mock_get):
        mock_get.side_effect = _pages(*[_city_page(SATURDAY)] * 3)
        result = weather.fetch_eccc_weather(SATURDAY)
        assert result["source"] == weather.ECCC_SOURCE
        wl = result["williams_lake"]
        assert (wl["high"], wl["low"], wl["morning_temp"]) == (0, -8, -4)
        assert "around -4 degrees this morning" in result["summary"]
        assert "High zero." in result["summary"]
        assert "km/h" not in result["summary"] and "15 k-p-h" in result["summary"]
        assert "100 Mile House chance of flurries, high 0" in result["summary"]
        assert [c.args[0].rsplit("/", 1)[1] for c in mock_get.call_args_list] == ["bc-76", "bc-7", "bc-64"]

    @patch("weather.requests.get")
    def test_gives_horsefly_no_figure(self, mock_get):
        # SOURCED OR UNSAID: there is no Horsefly city page.
        mock_get.side_effect = _pages(*[_city_page(SATURDAY)] * 3)
        result = weather.fetch_eccc_weather(SATURDAY)
        assert "Horsefly Lake" not in result["summary"]
        assert "nearest to Horsefly" in result["summary"]
        prompt = format_weather_for_prompt(result)
        assert "no Horsefly forecast" in prompt
        assert "home base at Horsefly Lake" not in prompt

    @patch("weather.requests.get")
    def test_no_forecast_for_today_is_none(self, mock_get):
        # A page from another day (or a changed schema) must not air as today's.
        mock_get.side_effect = _pages(*[_city_page(SATURDAY + timedelta(days=2))] * 3)
        assert weather.fetch_eccc_weather(SATURDAY) is None

    @patch("weather.requests.get")
    def test_warnings_are_read_once(self, mock_get):
        snow = [{"description": {"en": "Snowfall warning in effect.", "fr": "x"}}]
        mock_get.side_effect = _pages(*[_city_page(SATURDAY, warnings=snow)] * 3)
        summary = weather.fetch_eccc_weather(SATURDAY)["summary"]
        assert summary.count("Environment Canada alert: Snowfall warning in effect.") == 1

    @patch("weather.requests.get")
    def test_slide_lists_the_three_towns(self, mock_get):
        mock_get.side_effect = _pages(*[_city_page(SATURDAY)] * 3)
        slide = weather_slide_data(weather.fetch_eccc_weather(SATURDAY))
        assert slide["source"] == "Environment Canada"
        assert [l["name"] for l in slide["locations"]] == ["Williams Lake", "100 Mile House", "Quesnel"]
        assert slide["locations"][0] == {"name": "Williams Lake", "temp": -4,
                                         "conditions": "Chance of flurries", "high": 0, "low": -8}

    @patch("weather.time.sleep")
    @patch("weather.requests.get")
    def test_stands_in_when_open_meteo_times_out(self, mock_get, _sleep):
        import requests as _requests
        today = datetime.now(ZoneInfo("America/Vancouver")).date()
        timeout = _requests.exceptions.ReadTimeout("read timeout=10")
        # Horsefly twice (one retry), then straight to Environment Canada's three pages.
        mock_get.side_effect = [timeout, timeout] + _pages(*[_city_page(today)] * 3)
        result = fetch_weather()
        assert result["source"] == "Environment Canada"
        assert mock_get.call_count == 5

    @patch("weather.requests.get")
    def test_open_meteo_nights_say_so(self, mock_get):
        mock_get.side_effect = _make_mock_get(*[_mock_api_response()] * 5).side_effect
        assert fetch_weather()["source"] == "Open-Meteo"
