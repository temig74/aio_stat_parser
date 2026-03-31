from pydantic_settings import BaseSettings, SettingsConfigDict  # pip install pydantic-settings
from pydantic import SecretStr, Field


class Settings(BaseSettings):
    bot_token: SecretStr
    max_message_len: int
    en_username: str
    en_password: SecretStr
    user_agent: str
    admin_chat_id: int
    bot_en_id: str
    admins_raw: str = Field(alias='ADMINS')
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8')

    @property
    def admins(self) -> list[str]:
        return [name.strip() for name in self.admins_raw.split(',')]


config = Settings()
