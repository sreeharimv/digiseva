from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime
import uuid


ServiceType = Literal["subscription", "bill"]
SubscriptionCategory = Literal["OTT", "Cloud", "AI", "Telecom", "Domain", "VPN", "Software", "Other"]
BillCategory = Literal["Electricity", "FTTH", "LPG", "DTH", "Water", "Maintenance", "Other"]
Cycle = Literal["weekly", "monthly", "quarterly", "half-yearly", "yearly", "one-time"]


class Service(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    type: ServiceType
    category: str
    amount: float
    currency: str = "INR"
    cycle: Cycle
    next_due: str  # ISO date string YYYY-MM-DD
    payment_method: str = ""
    auto_debit: bool = False
    paid_current_cycle: bool = False
    notes: str = ""
    active: bool = True
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class ServiceCreate(BaseModel):
    name: str
    type: ServiceType
    category: str
    amount: float
    currency: str = "INR"
    cycle: Cycle
    next_due: str
    payment_method: str = ""
    auto_debit: bool = False
    notes: str = ""
    active: bool = True


class ServiceUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[ServiceType] = None
    category: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    cycle: Optional[Cycle] = None
    next_due: Optional[str] = None
    payment_method: Optional[str] = None
    auto_debit: Optional[bool] = None
    paid_current_cycle: Optional[bool] = None
    notes: Optional[str] = None
    active: Optional[bool] = None


class SummaryResponse(BaseModel):
    monthly_total: float
    upcoming: list
    overdue: list
    paid_count: int
    total_count: int
