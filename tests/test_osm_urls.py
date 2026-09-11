from config import resolve_osm_urls

DEV = 'https://master.apis.dev.openstreetmap.org'


def test_defaults_to_live_osm_with_its_separate_api_host():
    assert resolve_osm_urls(None, None) == ('https://www.openstreetmap.org', 'https://api.openstreetmap.org')


def test_other_instance_serves_api_from_the_website_host():
    assert resolve_osm_urls(DEV, None) == (DEV, DEV)


def test_explicit_api_url_wins():
    assert resolve_osm_urls(DEV, 'https://api.example.org/') == (DEV, 'https://api.example.org')


def test_trailing_slash_is_stripped():
    assert resolve_osm_urls(f'{DEV}/', None) == (DEV, DEV)


def test_empty_values_fall_back_to_live_osm():
    # an empty line in .env sets the variable to ''
    assert resolve_osm_urls('', '') == ('https://www.openstreetmap.org', 'https://api.openstreetmap.org')
