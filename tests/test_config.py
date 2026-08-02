from app.config import Settings


def test_root_gateway_url_gets_openai_v1_path():
    settings = Settings(openai_base_url="https://example.com/")
    assert settings.openai_sdk_base_url == "https://example.com/v1/"


def test_existing_v1_path_is_preserved():
    settings = Settings(openai_base_url="https://example.com/v1")
    assert settings.openai_sdk_base_url == "https://example.com/v1/"
