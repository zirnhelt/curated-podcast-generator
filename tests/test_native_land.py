"""Territory confirmation before the show names a nation on air.

Ground truth for every case here is the 2026-09-18 Wild Spaces episode, which
asked four times whether "Sinixt or Tŝilhqot'in voices" shaped a prescribed
burn outside Castlegar. Sinixt is right; Tŝilhqot'in territory is the Chilcotin
plateau, ~600 km away, and the name came from the script prompt's standing
Cariboo context rather than from any source.

No test here touches the network. `territories_for_place` is monkeypatched, so
these fix the *logic* — the orthography folding, the in-region exemption, the
disconfirm-only contract — which is the half that decides whether a real lookup
produces a correction or a false accusation.
"""

import json

import pytest

import native_land
import podcast_generator as pg


# The real Native Land answer shape: GeoJSON features with properties.Name.
CASTLEGAR_TERRITORIES = ["Sinixt", "Ktunaxa ɁamakɁis", "Syilx tmixʷ"]
WILLIAMS_LAKE_TERRITORIES = ["Secwepemc (Shuswap)", "Tsilhqot'in"]


@pytest.fixture(autouse=True)
def _isolate_territory_cache(tmp_path, monkeypatch):
    """Never let a test write the committed territory cache."""
    monkeypatch.setattr(native_land, "_cache_path",
                        lambda: tmp_path / "native_land_cache.json")
    native_land._lookups_this_run = 0
    native_land.drain_degradations()


def _stub_lookup(monkeypatch, mapping):
    """Answer territory lookups from a dict; anything unlisted resolves to None."""
    monkeypatch.setattr(native_land, "territories_for_place",
                        lambda place: mapping.get(place.lower()))


class TestNameFolding:
    def test_orthographic_variants_are_one_nation(self):
        """Tŝilhqot'in, Tsilhqot'in and TSILHQOT'IN must not be three nations."""
        folded = {native_land._normalize(v)
                  for v in ["Tŝilhqot'in", "Tsilhqot'in", "TSILHQOT'IN", "Tsilhqot’in"]}
        assert len(folded) == 1

    def test_secwepemc_matches_its_native_land_spelling(self):
        """The map writes 'Secwepemc (Shuswap)'; the script writes 'Secwépemc'."""
        assert native_land.nation_matches_territories(
            "Secwépemc", ["Shuswap"], WILLIAMS_LAKE_TERRITORIES)

    def test_the_castlegar_pairing_does_not_match(self):
        """The failure this whole module exists for."""
        assert not native_land.nation_matches_territories(
            "Tŝilhqot'in", ["Tsilhqot'in", "Chilcotin"], CASTLEGAR_TERRITORIES)

    def test_sinixt_does_match_castlegar(self):
        """The other half of that sentence was correct and must stay."""
        assert native_land.nation_matches_territories(
            "Sinixt", ["Lakes", "Arrow Lakes"], CASTLEGAR_TERRITORIES)

    def test_short_aliases_never_match(self):
        """'Lakes' is a word; matching on it would flag half the Cariboo."""
        assert not native_land.nation_matches_territories("Xx", ["Lak"], ["Lakota"])


class TestTerritoryClaims:
    def test_flags_the_aired_castlegar_sentence(self, monkeypatch):
        _stub_lookup(monkeypatch, {"castlegar": CASTLEGAR_TERRITORIES})
        script = (
            "**RILEY:** The open question with Deer Park specifically is whether "
            "the burn plan outside Castlegar reflects actual Sinixt or "
            "Tŝilhqot'in input, or whether it checked a consultation box."
        )
        findings = pg.check_territory_claims(script)
        assert [f["nation"] for f in findings] == ["Tŝilhqot'in"]
        assert findings[0]["place"] == "Castlegar"

    def test_the_standing_land_acknowledgment_is_never_flagged(self, monkeypatch):
        """Spoken every single episode, correct, and names only local places.

        A check that fires on this is worse than no check: it would rewrite the
        show's own acknowledgment nightly.
        """
        _stub_lookup(monkeypatch, {})
        script = (
            "**RILEY:** Welcome to Cariboo Signals. Speaking to you from the "
            "traditional territories of the Secwépemc, Tŝilhqot'in, and Dakelh "
            "nations here in the Cariboo region."
        )
        assert pg.check_territory_claims(script) == []

    def test_local_coverage_is_not_checked(self, monkeypatch):
        """Williams Lake is on `local_places`; no lookup should even be asked."""
        asked = []
        monkeypatch.setattr(native_land, "territories_for_place",
                            lambda p: asked.append(p))
        script = ("**CASEY:** Williams Lake First Nation and the Secwépemc "
                  "leadership met in Quesnel about the corridor.")
        assert pg.check_territory_claims(script) == []
        assert asked == []

    def test_a_failed_lookup_produces_no_finding(self, monkeypatch):
        """Disconfirm-only: no answer must never read as a contradiction."""
        _stub_lookup(monkeypatch, {})          # every place resolves to None
        script = ("**RILEY:** Whether Tŝilhqot'in voices shaped the plan near "
                  "Castlegar is the open question.")
        assert pg.check_territory_claims(script) == []

    def test_an_empty_territory_list_produces_no_finding(self, monkeypatch):
        """The map covering nothing there is not evidence the nation is wrong."""
        _stub_lookup(monkeypatch, {"castlegar": []})
        script = ("**RILEY:** Whether Tŝilhqot'in voices shaped the plan near "
                  "Castlegar is the open question.")
        assert pg.check_territory_claims(script) == []

    def test_a_correct_offsite_pairing_is_left_alone(self, monkeypatch):
        """Naming Sinixt for Castlegar is right and must survive the check."""
        _stub_lookup(monkeypatch, {"castlegar": CASTLEGAR_TERRITORIES})
        script = ("**CASEY:** The Sinixt have been clear about the Castlegar "
                  "burn plan from the start.")
        assert pg.check_territory_claims(script) == []

    def test_sentence_initial_capital_is_not_a_place(self, monkeypatch):
        """'Whether' opens the sentence and is capitalized by grammar."""
        asked = []

        def _record(place):
            asked.append(place)
            return None

        monkeypatch.setattr(native_land, "territories_for_place", _record)
        pg.check_territory_claims(
            "**RILEY:** Whether the Tŝilhqot'in were consulted is unclear.")
        assert "Whether" not in asked

    def test_a_sentence_with_no_nation_costs_no_lookup(self, monkeypatch):
        asked = []
        monkeypatch.setattr(native_land, "territories_for_place",
                            lambda p: asked.append(p))
        pg.check_territory_claims(
            "**CASEY:** The burn near Castlegar covers 450 hectares.")
        assert asked == []


class TestLookupBudget:
    def test_unresolved_places_are_cached_so_they_cost_one_lookup_ever(
            self, monkeypatch, tmp_path):
        """A capitalized non-place must not be geocoded again every night."""
        calls = []
        monkeypatch.setattr(native_land, "geocode_place",
                            lambda p: calls.append(p) or None)
        assert native_land.territories_for_place("Notaplace") is None
        assert native_land.territories_for_place("Notaplace") is None
        assert calls == ["Notaplace"]

    def test_a_transport_failure_is_not_cached(self, monkeypatch):
        """A blip must retry tomorrow rather than pin 'unknown' forever."""
        monkeypatch.setattr(native_land, "geocode_place",
                            lambda p: {"lat": 49.3, "lon": -117.6, "name": p,
                                       "admin1": "British Columbia", "country": "CA"})
        monkeypatch.setattr(native_land, "territories_for_point",
                            lambda lat, lon: None)
        assert native_land.territories_for_place("Castlegar") is None
        cache = json.loads(native_land._cache_path().read_text()) \
            if native_land._cache_path().exists() else {}
        assert "castlegar" not in cache

    def test_the_per_run_ceiling_degrades_rather_than_spending(self, monkeypatch):
        monkeypatch.setattr(native_land, "geocode_place", lambda p: None)
        native_land._lookups_this_run = native_land.MAX_LOOKUPS_PER_RUN
        assert native_land.territories_for_place("Castlegar") is None
        assert any("ceiling" in d for d in native_land.drain_degradations())


class TestResponseParsing:
    def test_reads_a_bare_feature_list(self):
        payload = [{"type": "Feature", "properties": {"Name": "Sinixt"}},
                   {"type": "Feature", "properties": {"Name": "Ktunaxa"}}]
        assert native_land._territory_names(payload) == ["Sinixt", "Ktunaxa"]

    def test_reads_a_feature_collection(self):
        payload = {"type": "FeatureCollection",
                   "features": [{"properties": {"name": "Secwepemc"}}]}
        assert native_land._territory_names(payload) == ["Secwepemc"]

    def test_garbage_parses_to_empty_rather_than_raising(self):
        assert native_land._territory_names({"error": "no key"}) == []
        assert native_land._territory_names(None) == []


class TestPromptScoping:
    def test_the_cariboo_nations_are_scoped_to_the_cariboo(self):
        """The prompt fix is the load-bearing half; the check is the backstop.

        Without this sentence the writer is handed three nation names as
        general regional vocabulary, which is exactly how Tŝilhqot'in reached
        a Castlegar story.
        """
        prompts = pg.CONFIG["prompts"]
        for key in ("script_generation", "script_generation_system"):
            template = prompts[key]["template"]
            assert "A nation is named only for its own territory" in template
            assert "Naming the wrong people is worse than naming none" in template
