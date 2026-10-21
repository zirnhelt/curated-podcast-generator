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


class TestNotEveryCapitalIsANationOrAPlace:
    """2026-09-22 to 2026-10-09: four episodes flagged "Tŝilhqot'in named in
    connection with Lorne", from election coverage that named no nation at all.
    "Cariboo-Chilcotin" is a riding, not the people, and "Lorne" is the MLA's
    first name, which geocoded to the Maritimes and was checked against the
    Mi'kmaq map. The scrub then tried to delete a nation from the sentence.
    """

    ELECTION = ("**RILEY:** Our Tuesday rundown covered Cariboo-Chilcotin, with "
                "Lorne Doerkson for the Conservatives, Kathryn Askew for the NDP, "
                "and Douglas Gook for the Greens.")

    def _record(self, monkeypatch):
        asked = []
        monkeypatch.setattr(native_land, "territories_for_place",
                            lambda p: asked.append(p) or ["Mi’kma’ki", "Wabanaki"])
        return asked

    def test_the_aired_election_sentence_names_no_nation(self, monkeypatch):
        asked = self._record(monkeypatch)
        assert pg._nations_named_in(self.ELECTION) == []
        assert pg.check_territory_claims(self.ELECTION) == []
        assert asked == []

    def test_a_surname_that_is_an_alias_names_no_one(self):
        """Randy Thompson is a Green candidate, not the Nlaka'pamux."""
        assert pg._nations_named_in(
            "**RILEY:** And Randy Thompson for the Greens.") == []

    def test_a_region_named_for_its_people_is_still_a_region(self):
        """The 2026-10-09 script said plainly it would not guess at the nation."""
        sentence = ("**RILEY:** Global News reports on an Indigenous cultural burn "
                    "in the grasslands west of Osoyoos, in the Okanagan.")
        assert pg._nations_named_in(sentence) == []

    def test_a_place_alias_with_a_people_word_names_the_people(self):
        named = pg._nations_named_in(
            "**CASEY:** The Okanagan Nation Alliance led the burn near Osoyoos.")
        assert [d for d, _ in named] == ["Syilx"]
        assert [d for d, _ in pg._nations_named_in(
            "**CASEY:** The Squamish Nation runs the site.")] == ["Squamish"]

    def test_a_place_containing_a_nation_name_does_not_name_it(self):
        assert pg._nations_named_in(
            "**CASEY:** The ferry to Haida Gwaii leaves from Prince Rupert.") == []
        assert [d for d, _ in pg._nations_named_in(
            "**CASEY:** The Haida have managed those waters for millennia.")] == ["Haida"]

    def test_possessives_and_orthography_still_name_the_nation(self):
        for s in ("Whether Tŝilhqot'in's title extends there is the question.",
                  "Whether TSILHQOT’IN title extends there is the question.",
                  "The St'át'imc and Statimc spellings are one people."):
            assert pg._nations_named_in(s), s

    def test_a_person_is_one_phrase_not_a_first_name(self, monkeypatch):
        places = pg._territory_place_candidates(
            "**RILEY:** Tŝilhqot'in leaders met Lorne Doerkson in Castlegar.",
            {"tsilhqotin"})
        assert places == ["Lorne Doerkson", "Castlegar"]

    def test_a_phrase_holding_a_nation_name_is_dropped_whole(self):
        """Splitting "Randy Thompson" would leave "Randy" to geocode."""
        places = pg._territory_place_candidates(
            "**RILEY:** Sinixt elders and Randy Thompson met near Okanagan Nation "
            "Alliance offices in Castlegar's Deer Park.",
            {"sinixt", "thompson", "okanagan nation alliance"})
        assert places == ["Castlegar", "Deer Park"]

    def test_part_of_a_local_place_is_local(self, monkeypatch):
        asked = self._record(monkeypatch)
        pg.check_territory_claims(
            "**CASEY:** The Secwépemc burn near 100 Mile House went well.")
        assert asked == []

    def test_the_scrub_accepts_a_rewrite_that_keeps_the_riding_name(self, monkeypatch):
        """Only the people's name has to go; "Cariboo-Chilcotin" can stay."""
        original = ("**RILEY:** Tŝilhqot'in voices on the Castlegar burn, and the "
                    "Cariboo-Chilcotin race.")
        rewrite = "**RILEY:** Local voices on the Castlegar burn, and the Cariboo-Chilcotin race."

        class _Resp:
            content = []
            usage = None

        monkeypatch.setattr(pg, "get_anthropic_client", lambda: object())
        monkeypatch.setattr(pg, "api_retry", lambda f: _Resp())
        monkeypatch.setattr(pg, "_log_claude_usage", lambda r: None)
        monkeypatch.setattr(pg, "message_text",
                            lambda r: json.dumps({"rewrites": [rewrite]}))
        finding = {"sentence": original, "nation": "Tŝilhqot'in", "place": "Castlegar",
                   "territories": CASTLEGAR_TERRITORIES}
        assert pg.scrub_territory_claims(original, [finding]) == rewrite

        # A bare alias is still the people: swapping one in is refused.
        swapped = "**RILEY:** Chilcotin voices on the Castlegar burn, and the Cariboo-Chilcotin race."
        monkeypatch.setattr(pg, "message_text",
                            lambda r: json.dumps({"rewrites": [swapped]}))
        assert pg.scrub_territory_claims(original, [finding]) == original


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


class TestResponseDecoding:
    def test_a_charsetless_body_is_read_as_utf8(self, monkeypatch):
        """requests falls back to Latin-1 without a charset; Mi’kma’ki came back
        as "Miâkmaâki", and a mangled name can never match a nation."""
        body = json.dumps([{"properties": {"Name": "Mi’kma’ki"}},
                           {"properties": {"Name": "Tŝilhqot’in"}}],
                          ensure_ascii=False).encode("utf-8")

        class _Resp:
            content = body

            def raise_for_status(self):
                pass

            def json(self):
                return json.loads(body.decode("latin-1"))

        monkeypatch.setattr(native_land.requests, "get", lambda *a, **k: _Resp())
        names = native_land._territory_names(native_land._get_json("u", {}))
        assert names == ["Mi’kma’ki", "Tŝilhqot’in"]
        assert native_land.nation_matches_territories("Tŝilhqot'in", [], names)


class TestGeocoding:
    @staticmethod
    def _results(monkeypatch, results):
        monkeypatch.setattr(native_land, "_get_json", lambda url, params: {"results": results})

    @staticmethod
    def _r(name, admin1, lat, lon, pop=0):
        return {"name": name, "admin1": admin1, "country_code": "CA",
                "latitude": lat, "longitude": lon, "population": pop}

    def test_a_bc_place_resolves(self, monkeypatch):
        self._results(monkeypatch, [self._r("Castlegar", "British Columbia", 49.32, -117.66, 8000)])
        assert native_land.geocode_place("Castlegar")["lat"] == 49.32

    def test_a_same_named_place_outside_bc_is_not_taken(self, monkeypatch):
        """'Lorne' resolved to the Maritimes and was checked against Mi'kma'ki."""
        self._results(monkeypatch, [self._r("Lorne", "New Brunswick", 47.9, -66.1, 500)])
        assert native_land.geocode_place("Lorne") is None

    def test_a_partial_name_match_is_not_taken(self, monkeypatch):
        self._results(monkeypatch, [self._r("Lorne Creek", "British Columbia", 54.6, -128.4)])
        assert native_land.geocode_place("Lorne") is None

    def test_a_name_that_is_several_bc_places_is_ambiguous(self, monkeypatch):
        self._results(monkeypatch, [self._r("Stump Lake", "British Columbia", 50.37, -120.35, 50),
                                    self._r("Stump Lake", "British Columbia", 53.1, -123.9)])
        assert native_land.geocode_place("Stump Lake") is None


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
