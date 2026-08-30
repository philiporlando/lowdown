"""Tests for settings-derived values."""

from lowdown.config import Settings

# Settings reads the repo's .env by default; pass every field these tests care
# about explicitly so they don't depend on the developer's local key.
DEFAULT_TEMPLATE = "https://{s}.basemaps.cartocdn.com/{style}/{z}/{x}/{y}{r}.png"


def test_basemap_url_without_key_keeps_leaflet_placeholders():
    url = Settings(basemap_api_key=None, basemap_style="light_all").basemap_url
    assert url == "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"


def test_basemap_url_appends_key():
    url = Settings(basemap_api_key="abc123", basemap_style="light_all").basemap_url
    assert url.endswith("/light_all/{z}/{x}/{y}{r}.png?key=abc123")


def test_basemap_url_substitutes_style():
    url = Settings(
        basemap_api_key=None, basemap_style="rastertiles/voyager"
    ).basemap_url
    assert "/rastertiles/voyager/{z}/" in url


def test_basemap_url_joins_existing_query_string():
    url = Settings(
        basemap_api_key="abc123",
        basemap_style="light_all",
        basemap_url_template=DEFAULT_TEMPLATE + "?foo=1",
    ).basemap_url
    assert url.endswith("?foo=1&key=abc123")
