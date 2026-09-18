"""Confirm which nations' territory a place sits in, before the show names one.

On 2026-09-18 the Wild Spaces episode ran a deep dive on a BC Wildfire Service
prescribed burn near Deer Park Mountain outside **Castlegar** — West Kootenay,
about 600 km southeast of here — and asked four separate times, in the cold
open, the deep dive and the outro, whether "Sinixt or Tŝilhqot'in voices" had
shaped the burn plan. Sinixt is right. Tŝilhqot'in is one of the show's own
three acknowledgment nations, whose territory is the Chilcotin plateau and
comes nowhere near Castlegar.

Nothing in the pipeline was wrong about a fact it had. The script prompt hands
the writer the three Cariboo nations as standing regional context, the writer
reached for the vocabulary it was given, and no stage after it had any way to
know that a nation and a place had been put together that do not go together.
This module is that stage.

**It disconfirms; it does not attribute.** Native Land Digital says plainly
that its maps are crowd-sourced, are not authoritative, and must not be used to
define legal or traditional boundaries — contacting the Nations directly is the
only way to do that. So the only question asked here is the negative one the
failure actually needs: *is the nation this script just named anywhere near the
place it named it for?* A lookup that comes back empty, errors, or is merely
unsure changes nothing and ships the line as written. The show's SOURCED OR
UNSAID rule is not weakened by a crowd-sourced map, and it must not be
strengthened by one either.

Two keyless lookups, both cached to disk forever, both bounded per run:

  place name  --Open-Meteo geocoding-->  lat/lon  --Native Land-->  territories

Open-Meteo is already the show's weather provider, so the geocoder adds a path
on a vendor the pipeline depends on rather than a vendor it does not.
Native Land's API takes a key (`NATIVE_LAND_API_KEY`, a repository *secret* —
unlike a model name, this one is a credential); it is sent when present and the
request is made without one when it is not, so a missing or expired key costs
the confirmation and never the episode.

Same circular-import constraint as `weekly_anchor` and `gemini_tts`: this
module records its own degradations and the script stage drains them.
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

import config_loader

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
NATIVE_LAND_URL = "https://native-land.ca/api/index.php"

# Both services are small, free and normally answer in well under a second.
# A read timeout here is a blip and costs nothing but the confirmation, so
# there is one retry and then the check stands down.
REQUEST_TIMEOUT_S = 10
FETCH_ATTEMPTS = 2
RETRY_BACKOFF_S = 2

# Ceiling on network lookups per run. The check only runs at all when the
# finished script names a nation, and a script names two or three places
# alongside it, so this is a runaway bound rather than a working budget —
# the cache serves every place the show has talked about before.
MAX_LOOKUPS_PER_RUN = 8

# A geocoder will happily resolve "Deer Park" to Texas. A result is taken only
# when it is in Canada, and a British Columbia match wins over any other
# province, because everything this show discusses that is worth checking is
# in BC and a same-named town elsewhere would be checked against the wrong
# map entirely.
PREFERRED_COUNTRY = "CA"
PREFERRED_ADMIN1 = "British Columbia"

_degradations: List[str] = []
_lookups_this_run = 0


def drain_degradations() -> List[str]:
    """Hand the caller every degradation recorded since the last drain."""
    global _degradations
    drained, _degradations = _degradations, []
    return drained


def _cache_path() -> Path:
    # Same MEMORY_DIR indirection podcast_generator uses, so a multi-tenant
    # deployment keeps its territory cache beside the rest of its state.
    base = Path(os.environ.get("MEMORY_DIR", Path(__file__).parent)) / "podcasts"
    base.mkdir(parents=True, exist_ok=True)
    return base / "native_land_cache.json"


def _load_cache() -> Dict:
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8")) or {}
    except Exception:
        # The loaders elsewhere in this pipeline swallow a truncated JSON as {}
        # and so does this one — but this cache is a convenience, not history:
        # losing it costs a handful of repeat lookups, never a claim.
        return {}


def _save_cache(cache: Dict) -> None:
    try:
        config_loader.atomic_write_json(_cache_path(), cache, indent=2, ensure_ascii=False)
    except Exception as e:
        _degradations.append(f"territory cache not written ({e}) — lookups repeat next run")


def _get_json(url: str, params: Dict):
    """GET with one retry on a transport failure. Returns None on give-up.

    A malformed body is never re-asked: it would come back malformed.
    """
    last = None
    for attempt in range(FETCH_ATTEMPTS):
        try:
            r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_S)
            r.raise_for_status()
            return r.json()
        except ValueError as e:      # body is not JSON — a verdict, not a blip
            return None
        except Exception as e:
            last = e
            if attempt + 1 < FETCH_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_S)
    if last is not None:
        # The exception's own message carries the full request URL, and that URL
        # carries the API key. Only the exception TYPE is recorded, because a
        # degradation row is written to the job summary and the run report.
        _degradations.append(f"lookup failed ({type(last).__name__}) — claim left as written")
    return None


def geocode_place(place: str) -> Optional[Dict]:
    """Resolve a place name to {lat, lon, name, admin1, country}, or None."""
    data = _get_json(GEOCODE_URL, {"name": place, "count": 10, "language": "en",
                                   "format": "json"})
    if not data:
        return None
    results = [r for r in (data.get("results") or [])
               if r.get("country_code") == PREFERRED_COUNTRY]
    if not results:
        return None
    results.sort(key=lambda r: (r.get("admin1") != PREFERRED_ADMIN1,
                                -(r.get("population") or 0)))
    best = results[0]
    return {
        "lat": best.get("latitude"),
        "lon": best.get("longitude"),
        "name": best.get("name"),
        "admin1": best.get("admin1"),
        "country": best.get("country_code"),
    }


def _territory_names(payload) -> List[str]:
    """Pull territory names out of whichever shape the API returns.

    The endpoint has historically answered with a bare list of GeoJSON
    Features and, elsewhere, a FeatureCollection wrapping the same features.
    Both are accepted rather than guessed at, because the failure mode of
    guessing wrong is an empty list, and an empty list here reads as
    "no territory contradicts this claim".
    """
    features = []
    if isinstance(payload, list):
        features = payload
    elif isinstance(payload, dict):
        features = payload.get("features") or []
    names = []
    for f in features:
        if not isinstance(f, dict):
            continue
        props = f.get("properties") if isinstance(f.get("properties"), dict) else f
        name = props.get("Name") or props.get("name")
        if name:
            names.append(str(name))
    return names


def territories_for_point(lat: float, lon: float) -> Optional[List[str]]:
    """Territory names covering a coordinate, or None when the lookup failed.

    None and [] are deliberately different answers: None is "the map did not
    answer" and [] is "the map answered and covers nothing here". Only a
    non-empty list is ever evidence.
    """
    params = {"maps": "territories", "position": f"{lat},{lon}"}
    key = os.getenv("NATIVE_LAND_API_KEY")
    if key:
        params["key"] = key
    payload = _get_json(NATIVE_LAND_URL, params)
    if payload is None:
        return None
    return _territory_names(payload)


def territories_for_place(place: str) -> Optional[List[str]]:
    """Cached place-name -> territory names. None when it could not be resolved.

    Cached permanently: a town does not move, and the territory map changes on
    a scale of years rather than episodes. Delete
    `podcasts/native_land_cache.json` to refresh it.
    """
    global _lookups_this_run
    key = place.strip().lower()
    if not key:
        return None

    cache = _load_cache()
    if key in cache:
        entry = cache[key]
        return entry.get("territories") if entry.get("resolved") else None

    if _lookups_this_run >= MAX_LOOKUPS_PER_RUN:
        _degradations.append(
            f"per-run lookup ceiling ({MAX_LOOKUPS_PER_RUN}) reached — "
            f"'{place}' not checked"
        )
        return None
    _lookups_this_run += 1

    geo = geocode_place(place)
    if not geo or geo.get("lat") is None:
        # Not a place, or not a Canadian one. Cached as unresolved so the same
        # capitalized word is not geocoded again every night.
        cache[key] = {"resolved": False}
        _save_cache(cache)
        return None

    territories = territories_for_point(geo["lat"], geo["lon"])
    if territories is None:
        return None  # transport failure — not cached, so it retries tomorrow

    cache[key] = {
        "resolved": True,
        "lat": geo["lat"],
        "lon": geo["lon"],
        "admin1": geo.get("admin1"),
        "territories": territories,
    }
    _save_cache(cache)
    return territories


def _normalize(text: str) -> str:
    """Fold a nation or territory name for comparison.

    Orthography is the whole difficulty: Tŝilhqot'in, Tsilhqot'in, Chilcotin
    and Tsilhqot'in National Government are one nation, and a comparison that
    keeps diacritics, apostrophes or the word "Nation" would call every one of
    them a different one. Everything not a letter or digit is dropped and the
    marked characters are folded to their plain forms.
    """
    folded = (text or "").lower()
    for src, dst in (("ŝ", "s"), ("š", "s"), ("ś", "s"), ("é", "e"), ("è", "e"),
                     ("ā", "a"), ("ū", "u"), ("ł", "l"), ("ʼ", ""), ("’", ""),
                     ("‘", ""), ("'", "")):
        folded = folded.replace(src, dst)
    return re.sub(r"[^a-z0-9]+", "", folded)


def nation_matches_territories(nation: str, aliases: List[str],
                               territories: List[str]) -> bool:
    """True when the nation (or one of its aliases) appears in the territories.

    Substring in both directions on the folded forms, because Native Land
    names a territory "Secwepemc (Shuswap)" and the script says "Secwépemc",
    while elsewhere the script says "Williams Lake First Nation" against a
    territory simply named "Secwepemc". Either containing the other is a
    match — a false *match* only means the line ships as written, which is
    the safe direction for this check to fail in.
    """
    candidates = [_normalize(n) for n in ([nation] + list(aliases)) if n]
    folded_territories = [_normalize(t) for t in territories]
    for c in candidates:
        if len(c) < 4:
            continue
        for t in folded_territories:
            if c in t or t in c:
                return True
    return False
