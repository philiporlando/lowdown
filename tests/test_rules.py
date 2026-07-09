"""Tests for the low-altitude rule evaluation."""

from lowdown.config import Airport, Helipad, Settings
from lowdown.rules import evaluate


def _settings(**overrides) -> Settings:
    base = dict(
        apartment_lat=45.5152,
        apartment_lon=-122.6784,
        threshold_agl_ft=1000.0,
        obstacle_buffer_ft=0.0,
        airport_proximity_km=8.0,
        helipad_proximity_km=1.0,
        vertical_rate_excepted_fpm=500.0,
        airports=[Airport(code="PDX", lat=45.5887, lon=-122.5975)],
        helipads=[Helipad(code="HOSPITAL", lat=45.4993, lon=-122.6855)],
        elevation_provider="fixed",
        run_collector=False,
    )
    base.update(overrides)
    return Settings(**base)


def test_low_over_city_is_flagged():
    ev = evaluate(
        _settings(),
        lat=45.5152,
        lon=-122.6784,
        msl_ft=650.0,
        ground_elev_ft=50.0,  # 600 ft AGL
        vertical_rate_fpm=0.0,
        is_rotorcraft=False,
    )
    assert ev.is_low is True
    assert ev.agl_ft == 600.0
    assert ev.likely_approach_departure is False


def test_high_is_not_flagged():
    ev = evaluate(
        _settings(),
        lat=45.5152,
        lon=-122.6784,
        msl_ft=5050.0,
        ground_elev_ft=50.0,  # 5000 ft AGL
        vertical_rate_fpm=0.0,
        is_rotorcraft=False,
    )
    assert ev.is_low is False


def test_near_airport_is_annotated_exempt():
    # Low, but right on top of PDX -> likely takeoff/landing.
    ev = evaluate(
        _settings(),
        lat=45.5887,
        lon=-122.5975,
        msl_ft=350.0,
        ground_elev_ft=30.0,
        vertical_rate_fpm=0.0,
        is_rotorcraft=False,
    )
    assert ev.is_low is True
    assert ev.near_airport == "PDX"
    assert ev.likely_approach_departure is True


def test_steep_descent_is_annotated_exempt():
    ev = evaluate(
        _settings(),
        lat=45.5152,
        lon=-122.6784,
        msl_ft=650.0,
        ground_elev_ft=50.0,
        vertical_rate_fpm=-1200.0,
        is_rotorcraft=False,
    )
    assert ev.likely_approach_departure is True


def test_unknown_terrain_means_not_low():
    ev = evaluate(
        _settings(),
        lat=45.5152,
        lon=-122.6784,
        msl_ft=650.0,
        ground_elev_ft=None,
        vertical_rate_fpm=0.0,
        is_rotorcraft=False,
    )
    assert ev.is_low is False
    assert ev.agl_ft is None


def test_helipad_landing_is_exempt():
    # Low, right over the hospital helipad -> likely medevac landing.
    ev = evaluate(
        _settings(),
        lat=45.4993,
        lon=-122.6855,
        msl_ft=550.0,
        ground_elev_ft=50.0,
        vertical_rate_fpm=0.0,
        is_rotorcraft=False,
    )
    assert ev.is_low is True  # still recorded
    assert ev.near_helipad == "HOSPITAL"
    assert ev.is_exempt is True


def test_adsb_rotorcraft_is_exempt():
    ev = evaluate(
        _settings(),
        lat=45.5152,
        lon=-122.6784,
        msl_ft=650.0,
        ground_elev_ft=50.0,
        vertical_rate_fpm=0.0,
        is_rotorcraft=True,
    )
    assert ev.is_low is True
    assert ev.is_exempt is True
    assert ev.is_rotorcraft is True


def test_registry_rotorcraft_is_exempt_without_adsb_category():
    # No ADS-B rotorcraft flag, but the FAA registry says it's a helicopter.
    ev = evaluate(
        _settings(),
        lat=45.5152,
        lon=-122.6784,
        msl_ft=650.0,
        ground_elev_ft=50.0,
        vertical_rate_fpm=0.0,
        is_rotorcraft=False,
        aircraft_category="rotorcraft",
        aircraft_model="EUROCOPTER EC 135",
    )
    assert ev.is_exempt is True
    assert ev.is_rotorcraft is True
    assert ev.aircraft_type == "rotorcraft"
    assert ev.aircraft_model == "EUROCOPTER EC 135"


def test_registry_fixed_wing_low_is_not_exempt():
    # A genuine fixed-wing low over the city stays an unexplained violation.
    ev = evaluate(
        _settings(),
        lat=45.5152,
        lon=-122.6784,
        msl_ft=650.0,
        ground_elev_ft=50.0,
        vertical_rate_fpm=0.0,
        is_rotorcraft=False,
        aircraft_category="fixed-wing",
    )
    assert ev.is_low is True
    assert ev.is_exempt is False
    assert ev.exempt_reason is None
