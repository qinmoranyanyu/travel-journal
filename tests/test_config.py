from app.config import Settings, get_settings


def test_root_gateway_url_gets_openai_v1_path():
    settings = Settings(openai_base_url="https://example.com/")
    assert settings.openai_sdk_base_url == "https://example.com/v1/"


def test_existing_v1_path_is_preserved():
    settings = Settings(openai_base_url="https://example.com/v1")
    assert settings.openai_sdk_base_url == "https://example.com/v1/"


def test_image_generation_defaults_to_controlled_concurrency():
    settings = Settings()
    assert settings.image_generation_interval_seconds == 10
    assert settings.image_generation_concurrency == 3


def test_image_generation_concurrency_loads_from_environment(monkeypatch):
    monkeypatch.setenv("IMAGE_GENERATION_CONCURRENCY", "5")
    get_settings.cache_clear()
    try:
        assert get_settings().image_generation_concurrency == 5
    finally:
        get_settings.cache_clear()
