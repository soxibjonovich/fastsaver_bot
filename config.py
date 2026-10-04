from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ALLOWED_USERS: list[int]
    ADMIN: int
    API_KEY: SecretStr
    API_URL: str = "https://api.fastsaver.io/v1"
    BOT_TOKEN: SecretStr

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()

if __name__ == "__main__":
    # testing viability
    print(settings)
    assert isinstance(settings.ALLOWED_USERS, list) == True
    assert len(settings.ALLOWED_USERS) > 0
