from pydantic_settings import BaseSettings, SettingsConfigDict  # pip install pydantic-settings
from pydantic import SecretStr


class Settings(BaseSettings):
    bot_token: SecretStr
    max_message_len: int
    en_username: str
    en_password: SecretStr
    user_agent: str
    admin_chat_id: int
    bot_en_id: str
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8')


config = Settings()
