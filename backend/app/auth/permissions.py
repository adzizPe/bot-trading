from enum import Enum


class RoleName(str, Enum):
    VIEWER = "VIEWER"
    OPERATOR = "OPERATOR"
    RISK_ADMIN = "RISK_ADMIN"
    EXECUTION_ADMIN = "EXECUTION_ADMIN"
    SUPER_ADMIN = "SUPER_ADMIN"


class Permission(str, Enum):
    READ_DASHBOARD = "dashboard:read"
    READ_MARKET = "market:read"
    READ_SIGNALS = "signals:read"
    READ_STATISTICS = "statistics:read"
    MT5_CONTROL = "mt5:control"
    ANALYSIS_GENERATE = "analysis:generate"
    PAPER_CONTROL = "paper:control"
    PAPER_TRADE = "paper:trade"
    BACKTEST_SUBMIT = "backtest:submit"
    BACKTEST_CANCEL = "backtest:cancel"
    RISK_SETTINGS_UPDATE = "risk:settings:update"
    TRADE_PLAN_CREATE = "trade-plan:create"
    RISK_FEASIBILITY = "risk:feasibility"
    DEMO_EXECUTE = "demo:execute"
    DEMO_POSITION_MANAGE = "demo:position:manage"
    DEMO_SETTINGS_UPDATE = "demo:settings:update"
    EMERGENCY_STOP = "emergency-stop:execute"
    SAFETY_RESET = "safety:reset"
    USER_MANAGE = "users:manage"
    ROLE_MANAGE = "roles:manage"
    SESSION_INVALIDATE = "sessions:invalidate"


READ_PERMISSIONS = frozenset({
    Permission.READ_DASHBOARD, Permission.READ_MARKET,
    Permission.READ_SIGNALS, Permission.READ_STATISTICS,
})
OPERATOR_PERMISSIONS = READ_PERMISSIONS | frozenset({
    Permission.MT5_CONTROL, Permission.ANALYSIS_GENERATE,
    Permission.PAPER_CONTROL, Permission.PAPER_TRADE,
    Permission.BACKTEST_SUBMIT, Permission.BACKTEST_CANCEL,
})
ROLE_PERMISSIONS: dict[RoleName, frozenset[Permission]] = {
    RoleName.VIEWER: READ_PERMISSIONS,
    RoleName.OPERATOR: OPERATOR_PERMISSIONS,
    RoleName.RISK_ADMIN: OPERATOR_PERMISSIONS | frozenset({
        Permission.RISK_SETTINGS_UPDATE, Permission.TRADE_PLAN_CREATE,
        Permission.RISK_FEASIBILITY,
    }),
    RoleName.EXECUTION_ADMIN: READ_PERMISSIONS | frozenset({
        Permission.DEMO_EXECUTE, Permission.DEMO_POSITION_MANAGE,
        Permission.EMERGENCY_STOP,
    }),
    RoleName.SUPER_ADMIN: frozenset(Permission),
}
