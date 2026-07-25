from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
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
    auth_access_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    auth_refresh_ttl_seconds: int = Field(default=604800, ge=3600, le=2592000)
    auth_cookie_secure: bool = False
    auth_trusted_proxies: list[str] = []
    auth_login_rate_limit: int = Field(default=10, ge=1, le=1000)
    auth_login_rate_window_seconds: int = Field(default=300, ge=1, le=3600)
    auth_account_lockout_attempts: int = Field(default=5, ge=1, le=100)
    auth_account_lockout_seconds: int = Field(default=900, ge=1, le=86400)
    mt5_login: int | None = None
    mt5_password: SecretStr | None = None
    mt5_server: str | None = None
    mt5_path: Path | None = None
    mt5_symbol: str = "XAUUSD"
    mt5_connect_retries: int = Field(default=3, ge=1, le=10)
    mt5_retry_delay_seconds: float = Field(default=1.0, ge=0, le=60)
    mt5_timeout_ms: int = Field(default=10_000, ge=100, le=120_000)
    mt5_vendor_timeout_ms: int = Field(default=3_000, ge=10, le=60_000)
    mt5_order_send_timeout_ms: int = Field(default=10_000, ge=10, le=120_000)
    mt5_heartbeat_timeout_ms: int = Field(default=1_000, ge=10, le=10_000)
    mt5_heartbeat_interval_seconds: float = Field(default=5.0, ge=0.1, le=300)
    mt5_recovery_retries: int = Field(default=3, ge=1, le=10)
    mt5_recovery_delay_seconds: float = Field(default=1.0, ge=0, le=60)
    market_tick_cache_ttl_seconds: float = 0.25
    market_candle_cache_ttl_seconds: float = 1.0
    market_cache_max_entries: int = 128
    market_max_candles: int = 1000
    market_ws_interval_seconds: float = Field(default=1.0, ge=0.05, le=60.0)
    ws_max_connections_per_user: int = Field(default=5, ge=1, le=100)
    ws_max_connections_per_ip: int = Field(default=20, ge=1, le=500)
    ws_max_total_connections: int = Field(default=200, ge=1, le=5000)
    ws_idle_timeout_seconds: float = Field(default=120.0, ge=0.05, le=3600)
    ws_heartbeat_interval_seconds: float = Field(default=15.0, ge=0.05, le=300)
    ws_heartbeat_timeout_seconds: float = Field(default=10.0, ge=0.05, le=300)
    ws_client_buffer_size: int = Field(default=32, ge=1, le=1000)
    ws_slow_client_drop_limit: int = Field(default=8, ge=1, le=1000)
    ws_send_timeout_seconds: float = Field(default=5.0, ge=0.05, le=60)
    ws_handshake_rate_limit: int = Field(default=30, ge=1, le=1000)
    ws_handshake_rate_window_seconds: float = Field(default=60.0, ge=0.05, le=3600)
    ws_reconnect_rate_limit: int = Field(default=20, ge=1, le=1000)
    ws_reconnect_rate_window_seconds: float = Field(default=60.0, ge=0.05, le=3600)
    ws_subscribe_rate_limit: int = Field(default=20, ge=1, le=1000)
    ws_subscribe_rate_window_seconds: float = Field(default=60.0, ge=0.05, le=3600)
    ws_session_revalidate_seconds: float = Field(default=30.0, ge=0.05, le=3600)
    max_backtest_jobs: int = Field(default=1, ge=1, le=8)
    max_pending_jobs: int = Field(default=3, ge=1, le=100)
    max_candles: int = Field(default=100_000, ge=100, le=250_000)
    max_date_range_days: int = Field(default=365, ge=1, le=3650)
    max_csv_size_mb: int = Field(default=50, ge=1, le=500)
    max_csv_rows: int = Field(default=100_000, ge=100, le=250_000)
    max_memory_budget_mb: int = Field(default=512, ge=64, le=4096)
    job_timeout_minutes: int = Field(default=30, ge=1, le=1440)
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
    safety_enabled: bool = True
    safety_max_spread_points: float = Field(default=300.0, gt=0)
    safety_active_sessions: str = "LONDON,NEW_YORK,ASIA"
    safety_custom_timezone: str = "UTC"
    safety_custom_start_hour: int = Field(default=0, ge=0, le=23)
    safety_custom_end_hour: int = Field(default=24, ge=1, le=24)
    safety_news_required: bool = False
    safety_news_blackout_before_minutes: int = Field(default=30, ge=0, le=1440)
    safety_news_blackout_after_minutes: int = Field(default=30, ge=0, le=1440)
    safety_circuit_error_threshold: int = Field(default=5, ge=1, le=100)
    safety_circuit_window_minutes: int = Field(default=30, ge=1, le=1440)
    safety_circuit_lock_minutes: int = Field(default=30, ge=1, le=1440)
    safety_heartbeat_interval_seconds: int = Field(default=5, ge=1, le=300)

    @field_validator(
        "mt5_login", "mt5_password", "mt5_server", "mt5_path", mode="before",
    )
    @classmethod
    def empty_mt5_values_are_none(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def secure_production_cookies(self) -> "Settings":
        if self.app_env.lower() == "production" and not self.auth_cookie_secure:
            raise ValueError("AUTH_COOKIE_SECURE must be true in production")
        return self

    model_config = SettingsConfigDict(
        env_file=(PROJECT_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
