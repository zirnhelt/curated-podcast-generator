"""Tests for the weather module."""

from unittest.mock import patch, MagicMock, call

from weather import (
    fetch_weather, format_weather_for_prompt, weather_slide_data, WMO_CODES,
    DRIVING_IMPACT_CODES, _has_driving_impact,
)


def _mock_api_response(current_temp=5, current_code=2, wind=10,
                       high=8, low=-3, precip=0, daily_code=3, max_wind=15):
    """Build a mock Open-Meteo JSON response for one location."""
    return {
        "current": {
            "temperature_2m": current_temp,
            "weather_code": current_code,
            "wind_speed_10m": wind,
        },
        "daily": {
            "temperature_2m_max": [high],
            "temperature_2m_min": [low],
            "precipitation_sum": [precip],
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
        horsefly = _mock_api_response(current_temp=-8, current_code=1)
        williams = _mock_api_response(current_temp=-5, current_code=2)
        mock_get.side_effect = [
            MagicMock(json=MagicMock(return_value=horsefly), raise_for_status=MagicMock()),
            MagicMock(json=MagicMock(return_value=williams), raise_for_status=MagicMock()),
        ]

        result = fetch_weather()
        assert result is not None
        assert "Horsefly Lake" in result["summary"]
        # Williams Lake should NOT appear (no driving impact with code 2)
        assert "Williams Lake" not in result["summary"]
        assert result["horsefly"]["current_temp"] == -8

    @patch("weather.requests.get")
    def test_williams_lake_included_on_snow(self, mock_get):
        """Williams Lake appears when there's snowfall (driving impact)."""
        horsefly = _mock_api_response(current_temp=-5, current_code=3)
        hundred_mile = _mock_api_response(current_temp=-4, current_code=2)
        williams = _mock_api_response(current_temp=-3, current_code=73)  # moderate snowfall
        quesnel = _mock_api_response(current_temp=-6, current_code=1)
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
        horsefly = _mock_api_response(current_temp=-2, current_code=2)
        hundred_mile = _mock_api_response(current_temp=-1, current_code=2)
        williams = _mock_api_response(current_temp=-1, current_code=66)  # freezing rain
        quesnel = _mock_api_response(current_temp=-3, current_code=1)
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
        horsefly = _mock_api_response(current_temp=10, current_code=2)
        hundred_mile = _mock_api_response(current_temp=11, current_code=2)
        williams = _mock_api_response(current_temp=12, current_code=2, max_wind=65)
        quesnel = _mock_api_response(current_temp=9, current_code=1)
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
        horsefly = _mock_api_response(precip=12.5)
        williams = _mock_api_response()
        mock_get.side_effect = [
            MagicMock(json=MagicMock(return_value=horsefly), raise_for_status=MagicMock()),
            MagicMock(json=MagicMock(return_value=williams), raise_for_status=MagicMock()),
        ]

        result = fetch_weather()
        assert "millimetres" in result["summary"]

    @patch("weather.requests.get")
    def test_cold_warning(self, mock_get):
        horsefly = _mock_api_response(low=-25, current_temp=-20)
        williams = _mock_api_response(low=-22, current_temp=-18)
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
        horsefly = _mock_api_response(current_temp=5)
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
        loc = {"current_code": 73, "daily_code": 2, "max_wind": 10}
        assert _has_driving_impact(loc) is True

    def test_clear_skies_no_impact(self):
        loc = {"current_code": 0, "daily_code": 1, "max_wind": 15}
        assert _has_driving_impact(loc) is False

    def test_high_wind_is_impact(self):
        loc = {"current_code": 0, "daily_code": 0, "max_wind": 55}
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
        "current_temp": temp, "current_code": code, "current_wind": 5,
        "high": temp + 5, "low": temp - 8, "precip": 0,
        "daily_code": 1, "tomorrow_code": 1, "max_wind": 10,
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
