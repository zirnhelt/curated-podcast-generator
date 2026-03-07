"""Tests for the weather module."""

import json
from unittest.mock import patch, MagicMock

from weather import fetch_weather, format_weather_for_prompt, WMO_CODES


class TestFetchWeather:
    def _mock_response(self, current_temp=5, current_code=2, wind=10,
                       high=8, low=-3, precip=0, daily_code=3, max_wind=15):
        """Build a mock Open-Meteo JSON response."""
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

    @patch("weather.requests.get")
    def test_returns_weather_dict(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = self._mock_response()
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = fetch_weather()
        assert result is not None
        assert result["current_temp"] == 5
        assert result["high"] == 8
        assert result["low"] == -3
        assert "Williams Lake" in result["summary"]

    @patch("weather.requests.get")
    def test_includes_precipitation(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = self._mock_response(precip=12.5)
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = fetch_weather()
        assert "millimetres" in result["summary"]

    @patch("weather.requests.get")
    def test_cold_warning(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = self._mock_response(low=-25, current_temp=-20)
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = fetch_weather()
        assert "extreme cold" in result["summary"].lower() or "bundle up" in result["summary"].lower()

    @patch("weather.requests.get")
    def test_returns_none_on_network_error(self, mock_get):
        mock_get.side_effect = Exception("network error")
        result = fetch_weather()
        assert result is None

    @patch("weather.requests.get")
    def test_returns_none_on_bad_json(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"unexpected": "shape"}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = fetch_weather()
        assert result is None


class TestFormatWeatherForPrompt:
    def test_returns_empty_string_when_none(self):
        assert format_weather_for_prompt(None) == ""

    def test_returns_weather_check_block(self):
        weather = {
            "summary": "Right now in Williams Lake it's 5 degrees.",
            "current_temp": 5,
        }
        result = format_weather_for_prompt(weather)
        assert "WEATHER CHECK" in result
        assert "Williams Lake" in result


class TestWMOCodes:
    def test_common_codes_have_descriptions(self):
        for code in [0, 1, 2, 3, 61, 71, 95]:
            assert code in WMO_CODES
