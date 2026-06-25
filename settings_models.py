from datetime import datetime
from enum import Enum
from typing import Dict, List, Literal, Annotated
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict, BeforeValidator

def reject_bool(value):
    if isinstance(value, bool):
        raise ValueError("Boolean values are not allowed for numeric fields")
    return value

BusinessFloat = Annotated[float, BeforeValidator(reject_bool)]

class BaseSettingsModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
    )

class CalculationType(str, Enum):
    fixed_per_order = "fixed_per_order"
    fixed_per_item = "fixed_per_item"
    per_m2 = "per_m2"
    per_linear_meter = "per_linear_meter"
    percent_of_materials = "percent_of_materials"

class MaterialPricingOverrides(BaseSettingsModel):
    profiles: Dict[str, BusinessFloat] = Field(default_factory=dict)
    fillings: Dict[str, BusinessFloat] = Field(default_factory=dict)
    hardware: Dict[str, BusinessFloat] = Field(default_factory=dict)
    extras: Dict[str, BusinessFloat] = Field(default_factory=dict)

    @field_validator("profiles", "fillings", "hardware", "extras")
    @classmethod
    def validate_overrides(cls, v: Dict[str, BusinessFloat]) -> Dict[str, BusinessFloat]:
        if len(v) > 100:
            raise ValueError("Maximum of 100 overrides allowed per category")
        for key, price in v.items():
            if not key or key.isspace():
                raise ValueError("Material ID cannot be empty or whitespace-only")
            if len(key) < 1 or len(key) > 100:
                raise ValueError("Material ID length must be between 1 and 100 characters")
            if price < 0.0:
                raise ValueError(f"Price for '{key}' cannot be negative")
        return v

class ResolvedMaterialPrices(BaseSettingsModel):
    profiles: Dict[str, BusinessFloat] = Field(default_factory=dict)
    fillings: Dict[str, BusinessFloat] = Field(default_factory=dict)
    hardware: Dict[str, BusinessFloat] = Field(default_factory=dict)
    extras: Dict[str, BusinessFloat] = Field(default_factory=dict)

    @field_validator("profiles", "fillings", "hardware", "extras")
    @classmethod
    def validate_prices(cls, v: Dict[str, BusinessFloat]) -> Dict[str, BusinessFloat]:
        for key, price in v.items():
            if not key or key.isspace():
                raise ValueError("Material ID cannot be empty or whitespace-only")
            if len(key) < 1 or len(key) > 100:
                raise ValueError("Material ID length must be between 1 and 100 characters")
            if price < 0.0:
                raise ValueError(f"Price for '{key}' cannot be negative")
        return v

class AdditionalCostSettings(BaseSettingsModel):
    id: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(..., min_length=1, max_length=100)
    calculation_type: CalculationType
    value: BusinessFloat = Field(..., ge=0.0)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=0, le=10000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or v.isspace():
            raise ValueError("Name cannot be empty or whitespace-only")
        return v

    @model_validator(mode="after")
    def validate_limits(self) -> "AdditionalCostSettings":
        if self.value < 0.0:
            raise ValueError("Value cannot be negative")
        if self.calculation_type == CalculationType.percent_of_materials:
            if self.value > 100.0:
                raise ValueError("Percent value cannot exceed 100")
        else:
            if self.value > 1_000_000_000.0:
                raise ValueError("Value exceeds the maximum allowed limit")
        return self

class CommercialSettings(BaseSettingsModel):
    markup_rate: BusinessFloat = Field(default=0.0, ge=0.0, le=500.0)
    discount_rate: BusinessFloat = Field(default=0.0, ge=0.0, le=100.0)

class TaxProfileSettings(BaseSettingsModel):
    name: str = Field(default="Без податку", min_length=1, max_length=100)
    rate: BusinessFloat = Field(default=0.0, ge=0.0, le=1.0)
    included_in_price: bool = Field(default=False)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or v.isspace():
            raise ValueError("Tax profile name cannot be empty or whitespace-only")
        return v

class HasAdditionalCosts(BaseSettingsModel):
    additional_costs: List[AdditionalCostSettings] = Field(default_factory=list)

    @field_validator("additional_costs")
    @classmethod
    def validate_costs_list(cls, v: List[AdditionalCostSettings]) -> List[AdditionalCostSettings]:
        if len(v) > 20:
            raise ValueError("Maximum of 20 additional costs allowed")
        ids = [cost.id for cost in v]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate additional cost IDs are not allowed")
        return v

class UserSettingsData(HasAdditionalCosts):
    currency: Literal["UAH"] = "UAH"
    pricing: MaterialPricingOverrides = Field(default_factory=MaterialPricingOverrides)
    commercial: CommercialSettings = Field(default_factory=CommercialSettings)
    tax_profile: TaxProfileSettings = Field(default_factory=TaxProfileSettings)

class UserSettingsUpdate(UserSettingsData):
    pass

class UserSettingsStored(UserSettingsData):
    schema_version: Literal[1] = 1
    updated_at: datetime

class UserSettingsResponse(UserSettingsData):
    schema_version: Literal[1] = 1
    updated_at: datetime
    is_default: bool = False

class PricingContext(HasAdditionalCosts):
    currency: Literal["UAH"] = "UAH"
    resolved_prices: ResolvedMaterialPrices
    commercial: CommercialSettings
    tax_profile: TaxProfileSettings
    settings_schema_version: Literal[1] = 1
