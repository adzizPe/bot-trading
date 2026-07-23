from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent
DEFAULT_DB_PATH = (BACKEND_DIR / "data" / "trading_bot.db").as_posix()


class Settings(BaseSettings):
    app_name: str = "XAU/USD Trading Bot"
    app_env: str = "development"
    app_debug: bool = False
    api_v1_prefix: str = "/api/v1"
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost"]
    database_url: str = f"sqlite+aiosqlite:///{DEFAULT_DB_PATH}"
    log_level: str = "INFO"
    mt5_login: int | None = None
    mt5_password: SecretStr | None = None
    mt5_server: str | None = None
    mt5_path: Path | None = None
    mt5_symbol: str = "XAUUSD"
    mt5_connect_retries: int = 3
    mt5_retry_delay_seconds: float = 1.0
    mt5_timeout_ms: int = 10_000
    market_tick_cache_ttl_seconds: float = 0.25
    market_candle_cache_ttl_seconds: float = 1.0
    market_cache_max_entries: int = 128
    market_max_candles: int = 1000
    market_ws_interval_seconds: float = 1.0
    analysis_ema_fast_period: int = 20
    analysis_ema_slow_period: int = 50
    analysis_rsi_period: int = 14
    analysis_atr_period: int = 14
    analysis_rsi_overbought: float = 70.0
    analysis_rsi_oversold: float = 30.0
    analysis_max_spread_points: float = 300.0
    analysis_candle_count: int = 120
    analysis_candle_body_atr_min: float = 0.2
    analysis_candle_close_location_min: float = 0.65
    analysis_structure_lookback: int = 20
    analysis_sr_lookback: int = 50
    analysis_swing_window: int = 2
    analysis_max_levels: int = 5
    analysis_strategy_name: str = "EMA_RSI_ATR_MTF_V1"
    risk_per_trade_percent: float = 1.0
    risk_max_daily_loss_percent: float = 3.0
    risk_max_daily_drawdown_percent: float = 5.0
    risk_max_consecutive_losses: int = 3
    risk_max_trades_per_day: int = 5
    risk_max_open_positions: int = 1
    risk_minimum_risk_reward: float = 1.5
    risk_target_risk_reward: float = 2.0
    risk_maximum_spread_points: float = 300.0
    risk_cooldown_minutes_after_loss: int = 30
    risk_use_equity_for_risk: bool = True
    risk_break_even_enabled: bool = False
    risk_trailing_stop_enabled: bool = False
    risk_stop_loss_method: str = "ATR"
    risk_atr_multiplier: float = 1.5
    risk_session_enabled: bool = True
    risk_session_start_hour_utc: int = 0
    risk_session_end_hour_utc: int = 24
    risk_session_weekdays: list[int] = [0, 1, 2, 3, 4]
    paper_initial_balance: float = 10_000.0
    paper_slippage_points: float = 0.0
    paper_commission_per_lot: float = 0.0
    paper_swap_long_per_lot: float = 0.0
    paper_swap_short_per_lot: float = 0.0
    paper_update_interval_seconds: float = 1.0
    paper_auto_trade_enabled: bool = False
    paper_maximum_open_positions: int = 1
    paper_allow_manual_trade_plan: bool = True
    paper_close_positions_on_stop: bool = False
    paper_emergency_close_positions: bool = True
    paper_break_even_enabled: bool = False
    paper_break_even_trigger_r: float = 1.0
    paper_trailing_stop_enabled: bool = False
    paper_trailing_stop_method: str = "POINTS"
    paper_trailing_distance_points: float = 0.0
    paper_trailing_atr_multiplier: float = 1.0
    demo_execution_enabled: bool = False
    demo_admin_token: SecretStr | None = None
    demo_execution_mode: Literal["MANUAL_DEMO"] = "MANUAL_DEMO"
    demo_magic: int = Field(default=9072026, gt=0, le=2_147_483_647)
    demo_comment: str = Field(default="bot-demo", min_length=1, max_length=31)
    demo_deviation_points: int = Field(default=20, ge=0, le=1000)
    demo_maximum_spread_points: float = Field(default=300.0, gt=0)
    demo_intent_ttl_seconds: int = Field(default=300, ge=1, le=3600)
    demo_emergency_close_positions: bool = False
    demo_trailing_stop_enabled: bool = False
    demo_trailing_distance_points: float = Field(default=0.0, ge=0)
    demo_rate_limit_requests: int = Field(default=60, ge=1, le=1000)
    demo_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    demo_rate_limit_max_clients: int = Field(default=1024, ge=1, le=10000)

    @field_validator(
        "mt5_login", "mt5_password", "mt5_server", "mt5_path",
        "demo_admin_token", mode="before",
    )
    @classmethod
    def empty_mt5_values_are_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("demo_admin_token")
    @classmethod
    def validate_demo_admin_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < 16:
            raise ValueError("DEMO_ADMIN_TOKEN must contain at least 16 characters")
        return value

    model_config = SettingsConfigDict(
        env_file=(PROJECT_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
