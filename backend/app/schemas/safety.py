from typing import Literal

from pydantic import Field

from app.schemas.demo import StrictRequest


class SafetyEmergencyRequest(StrictRequest):
    reason: str = Field(default="Manual emergency stop", min_length=1, max_length=255)
    confirmation_text: Literal["EMERGENCY STOP"]


class SafetyResetRequest(StrictRequest):
    confirmation_text: Literal["RESET EMERGENCY STOP"]
