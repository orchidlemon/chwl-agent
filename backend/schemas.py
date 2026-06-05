from pydantic import BaseModel
from typing import Optional, List, Any


class ChatRequest(BaseModel):
    message: str
    phase_hint: Optional[str] = None   # "start_plan" when user clicks the planning button
    original_request: Optional[str] = None  # user's first message, sent alongside start_plan


class PlanRequest(BaseModel):
    message: str
    tags: List[str] = []
    mode: str = "full"          # "light" | "full"
    scenario: str = "family"    # "family" | "friends"


class FulfillRequest(BaseModel):
    pass


class ExceptionConfirmRequest(BaseModel):
    confirmed: bool = True
    exception_type: str = "queue_spike"
    original_node_id: Optional[str] = None
    alternative: Optional[dict] = None
    recommended: Optional[dict] = None


class NodeActionRequest(BaseModel):
    node_id: str
    action: str          # "delete" | "pin" | "replace"
    force: bool = False  # True = bypass soft_lock warning (user already confirmed)


class ReportRequest(BaseModel):
    type: str       # "child_tired" | "queue_too_long" | "weather" | "lost"
    note: Optional[str] = None


class MemoryUpdateRequest(BaseModel):
    scope: str      # "session_facts" | "confirmed_preferences" | "derived_preferences"
    updates: dict


class InjectEventTextRequest(BaseModel):
    text: str


class NodeCheckinRequest(BaseModel):
    node_id: str
