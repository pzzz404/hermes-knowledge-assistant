from pathlib import Path
from datetime import date
from pydantic import Field
from pydantic_settings import BaseSettings,SettingsConfigDict

class Settings(BaseSettings):
    model_config=SettingsConfigDict(env_file='.env',extra='ignore')
    data_dir: Path=Path('.data')
    corpus_dir: Path=Path('content')
    hermes_url: str='http://127.0.0.1:8657'
    hermes_api_key: str=''
    hermes_model: str='openai/gpt-4.1-mini'
    adapter: str='hermes'
    timeout_seconds: float=Field(default=60,gt=0,le=120)
    max_parallel: int=Field(default=1,ge=1,le=2)
    session_per_minute: int=8
    ip_per_minute: int=20
    daily_generations: int=Field(default=80,ge=1,le=1000)
    generation_budget_override: int=0
    generation_budget_override_date: date|None=None
    session_ttl_hours: int=24
    cookie_secure: bool=False
    invite_token: str=''
    public_origin: str=''
