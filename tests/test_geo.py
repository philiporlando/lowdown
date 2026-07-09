"""Tests for geographic helpers."""

from lowdown.geo import bounding_box, haversine_m


def test_haversine_zero():
    assert haversine_m(45.5, -122.6, 45.5, -122.6) == 0.0


def test_haversine_known_distance():
    # PDX to downtown Portland is roughly 11-12 km.
    d = haversine_m(45.5887, -122.5975, 45.5152, -122.6784)
    assert 9_000 < d < 14_000


def test_bounding_box_contains_center_and_radius():
    lat, lon, r = 45.5152, -122.6784, 3000.0
    lamin, lomin, lamax, lomax = bounding_box(lat, lon, r)
    assert lamin < lat < lamax
    assert lomin < lon < lomax
    # A point at ~the radius due north should sit inside the box.
    north = lat + (r / 111_320)
    assert lamin < north < lamax
