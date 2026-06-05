from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime
import uuid


ServiceType = Literal["subscription", "bill", "income", "expense"]
SubscriptionCategory = Literal["OTT", "Cloud", "AI", "Telecom", "Domain", "VPN", "Software", "Other"]
BillCategory = Literal["Electricity", "FTTH", "LPG", "DTH", "Water", "Maintenance", "Other"]
IncomeCategory = Literal["Salary", "Freelance", "Rental", "Dividend", "Other"]
ExpenseCategory = Literal["EMI", "Credit Card", "Misc", "Other"]
Cycle = Literal["weekly", "monthly", "bi-monthly", "quarterly", "half-yearly", "yearly", "one-time"]


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
    # EMI fields (populated only for category="EMI")
    tenure_months: Optional[int] = None
    paid_instalments: int = 0
    # Credit card fields (populated only for category="Credit Card")
    credit_limit: Optional[float] = None
    outstanding_balance: float = 0.0
    statement_amount: float = 0.0


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
    tenure_months: Optional[int] = None
    credit_limit: Optional[float] = None
    outstanding_balance: float = 0.0
    statement_amount: float = 0.0


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
    tenure_months: Optional[int] = None
    paid_instalments: Optional[int] = None
    credit_limit: Optional[float] = None
    outstanding_balance: Optional[float] = None
    statement_amount: Optional[float] = None


class PaymentRecord(BaseModel):
    """Body for POST /api/services/{id}/payment — records a credit card payment."""
    amount: float
    notes: str = ""


class Investment(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    category: str          # Bank Account / Fixed Deposit / Mutual Fund / Stocks / Gold / PPF / EPF / NPS / Other
    current_value: float = 0.0
    invested_amount: float = 0.0
    institution: str = ""
    notes: str = ""
    active: bool = True
    last_updated: str = Field(default_factory=lambda: datetime.now().isoformat())
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class InvestmentCreate(BaseModel):
    name: str
    category: str
    current_value: float = 0.0
    invested_amount: float = 0.0
    institution: str = ""
    notes: str = ""


class InvestmentUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    current_value: Optional[float] = None
    invested_amount: Optional[float] = None
    institution: Optional[str] = None
    notes: Optional[str] = None
    active: Optional[bool] = None


class SummaryResponse(BaseModel):
    monthly_income: float
    monthly_outgo: float
    net_cashflow: float
    monthly_total: float  # alias for monthly_outgo — kept for backward compat
    upcoming: list
    overdue: list
    paid_count: int
    total_count: int


# ---------------------------------------------------------------------------
# Auth models
# ---------------------------------------------------------------------------

class UserCreate(BaseModel):
    username: str
    pin: str          # must be exactly 6 digits
    invite_code: str = ""


class UserLogin(BaseModel):
    username: str
    pin: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
