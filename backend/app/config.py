from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://clinic:clinic@localhost:5432/clinic"
    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
