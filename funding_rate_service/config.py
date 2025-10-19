"""
Configuration management for Funding Rate Service
"""

from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # Database
    database_url: str = "postgresql://funding_user:simba2001%23%23%23@localhost:5432/funding_rates"
    database_pool_min_size: int = 5
    database_pool_max_size: int = 20
    
    # Redis
    redis_url: str = "redis://localhost:6379/0"
    use_redis: bool = True
    
    # Service
    service_port: int = 8000
    service_host: str = "0.0.0.0"
    log_level: str = "INFO"
    environment: str = "development"
    
    # DEX APIs (REST endpoints)
    lighter_api_url: str = "https://mainnet.zklighter.elliot.ai"
    edgex_api_url: str = "https://pro.edgex.exchange"
    paradex_api_url: str = "https://api.prod.paradex.trade"
    grvt_api_url: str = "https://trade.prod.grvt.io"
    hyperliquid_api_url: str = "https://api.hyperliquid.xyz"
    
    # DEX WebSocket endpoints
    lighter_ws_url: str = "wss://mainnet.zklighter.elliot.ai/stream"
    edgex_ws_url: str = "wss://quote.edgex.exchange"
    paradex_ws_url: str = "wss://ws.prod.paradex.trade"
    grvt_ws_url: str = "wss://trade.prod.grvt.io"
    hyperliquid_ws_url: str = "wss://api.hyperliquid.xyz"
    
    # Collection settings
    collection_interval_seconds: int = 60
    max_concurrent_collections: int = 10
    collection_timeout_seconds: int = 30
    
    # Cache settings
    cache_ttl_seconds: int = 60
    cache_max_size_mb: int = 100
    
    # Adapter controls
    collection_disabled_dexes: List[str] = Field(
        default_factory=lambda: ["edgex"],
        description="DEX names to skip during funding collection",
    )
    
    @field_validator("collection_disabled_dexes", mode="before")
    @classmethod
    def _split_disabled_dexes(cls, value: List[str] | str | None) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            if not value:
                return []
            return [dex.strip() for dex in value.split(",") if dex.strip()]
        return value

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"  # Allow extra fields from .env (trading bot config)


# Global settings instance
settings = Settings()
