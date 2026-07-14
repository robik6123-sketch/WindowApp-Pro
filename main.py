from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from calculator import (
    WindowCalculator,
    CalculatorPricingError,
    UnknownMaterialError,
    MissingResolvedPriceError,
    apply_commercial_adjustments
)
from pricing_context_builder import (
    build_pricing_context,
    PricingContextBuilderError,
    InvalidGlobalCatalogError,
    UnknownMaterialOverrideError,
    PricingContextValidationError
)
from pdf_generator import generate_cart_pdf
import os
import json
import base64
import uuid
import struct
from datetime import datetime
import firebase_admin
from firebase_admin import auth
from auth_dependency import verify_firebase_token
from user_settings_repository import (
    UserSettingsRepository,
    InvalidUIDError,
    UserSettingsNotReadableError,
    UserSettingsInvalidDocumentError,
    UserSettingsWriteError
)
from settings_models import UserSettingsResponse, UserSettingsUpdate, UserSettingsStored, BusinessFloat, CalculationType
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator, ValidationError

class PanelRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
    )
    proportion: BusinessFloat = Field(default=100.0)
    type: str = Field(default="fixed")
    mosquito: bool = Field(default=False)

class CalculateRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
    )
    width: BusinessFloat
    height: BusinessFloat
    type: str = Field(default="rectangular")
    arc_height: Optional[BusinessFloat] = Field(default=None)
    material_type: str = Field(default="pvc")
    profile: str = Field(default="REHAU_Euro_70")
    glass: str = Field(default="glass_24")
    color: str = Field(default="white")
    panels: List[PanelRequest] = Field(
        default_factory=lambda: [PanelRequest(proportion=100.0, type="fixed")]
    )
    sill_length: BusinessFloat = Field(default=0.0)
    sill_width: BusinessFloat = Field(default=0.0)
    window_board: str = Field(default="none")
    window_board_length: BusinessFloat = Field(default=0.0)
    window_board_depth: BusinessFloat = Field(default=0.0)

    @model_validator(mode="after")
    def validate_arched_height(self) -> 'CalculateRequest':
        if self.type == "arched":
            if self.arc_height is None:
                raise ValueError("arc_height is required and cannot be null when type is arched")
        return self


app = FastAPI(title="WindowApp Pro API", version="2.0.0")
security = HTTPBearer()

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Calculator
USE_FIRESTORE = os.environ.get("USE_FIRESTORE", "true").lower() == "true"
calc = WindowCalculator(use_firestore=USE_FIRESTORE)

MAX_IMAGE_BYTES = 150 * 1024
MAX_ORDER_IMAGES_BYTES = 600 * 1024
MAX_IMAGE_WIDTH = 2000
MAX_IMAGE_HEIGHT = 4000


def _validate_storage_path_segment(value, argument_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{argument_name} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"{argument_name} must not have leading or trailing whitespace")
    if "/" in value or "\\" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{argument_name} contains invalid characters")


def build_order_image_storage_path(uid: str, order_id: str, item_index: int, image_key: str) -> str:
    _validate_storage_path_segment(uid, "uid")
    _validate_storage_path_segment(order_id, "order_id")
    if not isinstance(item_index, int) or isinstance(item_index, bool) or item_index < 0:
        raise ValueError("item_index must be a non-negative integer")
    if image_key not in ("front", "outside", "side"):
        raise ValueError("image_key must be front, outside, or side")
    return f"users/{uid}/orders/{order_id}/items/{item_index}/{image_key}.png"


def build_storage_image_reference(storage_path: str, image_metadata: dict) -> dict:
    if not isinstance(storage_path, str) or not storage_path:
        raise ValueError("storage_path must be a non-empty string")
    if storage_path != storage_path.strip():
        raise ValueError("storage_path must not have leading or trailing whitespace")
    if (storage_path.startswith("/") or storage_path.endswith("/") or "//" in storage_path
            or "\\" in storage_path or any(ord(char) < 32 or ord(char) == 127 for char in storage_path)):
        raise ValueError("storage_path is invalid")
    if not isinstance(image_metadata, dict):
        raise ValueError("image_metadata must be a dictionary")
    for field in ("width", "height", "size_bytes"):
        value = image_metadata.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"image_metadata {field} must be a positive integer")
    return {
        "storage_path": storage_path,
        "content_type": "image/png",
        "width": image_metadata["width"],
        "height": image_metadata["height"],
        "size_bytes": image_metadata["size_bytes"],
    }


def validate_png_data_url(image_data_url, item_index: int, image_key: str):
    if not isinstance(image_data_url, str):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Item {item_index} image {image_key} must be a string")
    prefix = "data:image/png;base64,"
    if not image_data_url.startswith(prefix):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Item {item_index} image {image_key} has invalid prefix")
    payload_str = image_data_url[len(prefix):]
    if not payload_str:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Item {item_index} image {image_key} has empty payload")
    try:
        decoded = base64.b64decode(payload_str, validate=True)
    except Exception:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Item {item_index} image {image_key} has invalid base64 content")
    if not decoded.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Item {item_index} image {image_key} is not a valid PNG image")
    if len(decoded) < 29:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Item {item_index} image {image_key} has invalid PNG structure")
    try:
        chunk_length = struct.unpack(">I", decoded[8:12])[0]
    except Exception:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Item {item_index} image {image_key} has invalid PNG structure")
    if decoded[12:16] != b"IHDR" or chunk_length != 13:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Item {item_index} image {image_key} has invalid PNG structure")
    try:
        width, height = struct.unpack(">II", decoded[16:24])
    except Exception:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Item {item_index} image {image_key} has invalid PNG structure")
    if width <= 0 or height <= 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Item {item_index} image {image_key} has invalid PNG dimensions")
    if width > MAX_IMAGE_WIDTH or height > MAX_IMAGE_HEIGHT:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Item {item_index} image {image_key} exceeds allowed PNG dimensions of {MAX_IMAGE_WIDTH}x{MAX_IMAGE_HEIGHT}")
    decoded_size = len(decoded)
    if decoded_size > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Item {item_index} image {image_key} exceeds allowed size of 150 KB")
    return decoded, {"width": width, "height": height, "size_bytes": decoded_size}

async def get_current_user(res: HTTPAuthorizationCredentials = Depends(security)):
    """Verifies Firebase ID Token"""
    try:
        decoded_token = auth.verify_id_token(res.credentials)
        return decoded_token
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")

def get_authenticated_uid(current_user: dict) -> str:
    uid = current_user.get("uid") if isinstance(current_user, dict) else None
    if not isinstance(uid, str) or not uid.strip():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication context is invalid",
        )
    return uid

def get_settings_repo() -> UserSettingsRepository:
    db = getattr(calc, "db", None)
    if not USE_FIRESTORE or db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User settings are temporarily unavailable",
        )
    return UserSettingsRepository(db)

def get_owned_order_or_404(order_id: str, uid: str) -> dict:
    if not USE_FIRESTORE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is temporarily unavailable"
        )
    try:
        doc_ref = calc.db.collection('orders').document(order_id).get()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal Server Error"
        ) from exc

    if not doc_ref.exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Замовлення не знайдено"
        )

    data = doc_ref.to_dict()
    if data is None or not isinstance(data, dict):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Замовлення не знайдено"
        )

    owner_uid = data.get("owner_uid")
    if not isinstance(owner_uid, str) or owner_uid != uid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Замовлення не знайдено"
        )

    return data

@app.get("/")
async def root():
    """Serves the main frontend interface"""
    return FileResponse("index.html")

@app.post("/api/calculate")
def calculate(
    order: CalculateRequest,
    current_user: dict = Depends(verify_firebase_token),
    repo: UserSettingsRepository = Depends(get_settings_repo),
):
    try:
        if order.width > 4000 or order.height > 3000:
            raise HTTPException(status_code=400, detail="Габарити перевищують інженерні норми")

        uid = get_authenticated_uid(current_user)
        settings_result = repo.get_user_settings(uid)

        pricing_context = build_pricing_context(
            calc.materials,
            settings_result.settings,
        )

        order_dict = order.model_dump(exclude_unset=True)

        calculation_result = calc.calculate_project(
            order_dict,
            pricing_context,
        )

        return calculation_result
    except InvalidUIDError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication context is invalid",
        ) from exc
    except UserSettingsNotReadableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User settings are temporarily unavailable",
        ) from exc
    except UserSettingsInvalidDocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored user settings are invalid",
        ) from exc
    except InvalidGlobalCatalogError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Внутрішня помилка конфігурації",
        ) from exc
    except UnknownMaterialOverrideError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Внутрішня помилка конфігурації",
        ) from exc
    except PricingContextValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Внутрішня помилка розрахунку ціни",
        ) from exc
    except PricingContextBuilderError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Внутрішня помилка розрахунку ціни",
        ) from exc
    except MissingResolvedPriceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Внутрішня помилка розрахунку ціни",
        ) from exc
    except UnknownMaterialError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Невідомий матеріал або конфігурація",
        ) from exc
    except CalculatorPricingError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Помилка конфігурації калькулятора",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal Server Error",
        ) from exc

def calculate_order_commercials(
    items_breakdowns: list,
    pricing_context
) -> dict:
    total_materials = 0.0
    total_local_costs = 0.0
    for ib in items_breakdowns:
        if (
            "materials_subtotal" not in ib
            or "additional_costs_total" not in ib
            or "additional_costs_breakdown" not in ib
        ):
            raise CalculatorPricingError("Missing required item-level breakdown fields")

        m = ib["materials_subtotal"]
        l = ib["additional_costs_total"]
        ac_breakdown = ib["additional_costs_breakdown"]

        if (
            not isinstance(m, (int, float)) or isinstance(m, bool)
            or not isinstance(l, (int, float)) or isinstance(l, bool)
            or not isinstance(ac_breakdown, list)
        ):
            raise CalculatorPricingError("Invalid item-level breakdown field types")

        total_materials += m
        total_local_costs += l

    order_costs_breakdown = []
    order_costs_total = 0.0

    # Process order-level costs (fixed_per_order)
    enabled_order_costs = [
        (idx, cost) for idx, cost in enumerate(pricing_context.additional_costs)
        if cost.enabled and cost.calculation_type == CalculationType.fixed_per_order
    ]
    sorted_order_costs = sorted(enabled_order_costs, key=lambda x: (x[1].sort_order, x[0]))

    for idx, cost in sorted_order_costs:
        raw_amount = float(cost.value)
        rounded_amount = round(raw_amount, 2)
        order_costs_total += rounded_amount
        order_costs_breakdown.append({
            "id": cost.id,
            "name": cost.name,
            "calculation_type": "fixed_per_order",
            "value": float(cost.value),
            "amount": rounded_amount
        })

    total_additional_costs = round(total_local_costs + order_costs_total, 2)

    # Apply shared adjustments
    order_result = apply_commercial_adjustments(
        materials_subtotal=total_materials,
        additional_costs_total=total_additional_costs,
        additional_costs_breakdown=[],  # Separate details for order breakdown
        commercial_settings=pricing_context.commercial,
        tax_profile=pricing_context.tax_profile
    )

    # Collect all items breakdowns combined for additional_costs_breakdown
    combined_additional_costs_breakdown = []
    for ib in items_breakdowns:
        combined_additional_costs_breakdown.extend(ib.get("additional_costs_breakdown", []))
    combined_additional_costs_breakdown.extend(order_costs_breakdown)

    order_result["items_materials_subtotal"] = total_materials
    order_result["item_level_additional_costs_total"] = total_local_costs
    order_result["item_level_additional_costs_breakdown"] = []
    for ib in items_breakdowns:
        order_result["item_level_additional_costs_breakdown"].extend(ib.get("additional_costs_breakdown", []))

    order_result["order_level_additional_costs_total"] = order_costs_total
    order_result["order_level_additional_costs_breakdown"] = order_costs_breakdown
    order_result["additional_costs_total"] = total_additional_costs
    order_result["additional_costs_breakdown"] = combined_additional_costs_breakdown

    return order_result

@app.post("/api/create-order")
def create_order(
    cart: dict,
    current_user: dict = Depends(verify_firebase_token),
    repo: UserSettingsRepository = Depends(get_settings_repo),
):
    try:
        uid = get_authenticated_uid(current_user)

        # 1. Structure checks
        if not isinstance(cart, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid cart structure"
            )
        items = cart.get("items")
        if not isinstance(items, list) or len(items) == 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Items must be a non-empty list"
            )

        # 2. Get user settings
        settings_result = repo.get_user_settings(uid)
        pricing_context = build_pricing_context(
            calc.materials,
            settings_result.settings,
        )

        # 3. Filter out fixed_per_order costs for item-level calculations
        item_only_costs = [
            cost.model_copy() for cost in pricing_context.additional_costs
            if cost.calculation_type != CalculationType.fixed_per_order
        ]
        item_pricing_context = pricing_context.model_copy(
            update={"additional_costs": item_only_costs}
        )

        total_order_images_size = 0
        trusted_items = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Item {idx} must be an object"
                )
            inp = item.get("input")
            if not isinstance(inp, dict):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Item {idx} must contain input object"
                )

            # Separate images
            input_dict = {**inp}
            images = input_dict.pop("images", None)

            # Validate images contract
            if images is not None:
                if not isinstance(images, dict):
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Item {idx} images must be an object"
                    )
                for k, v in images.items():
                    if k not in ("front", "outside", "side"):
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"Item {idx} image key {k} is invalid"
                        )
                    _, image_metadata = validate_png_data_url(v, idx, k)
                    total_order_images_size += image_metadata["size_bytes"]
                    if total_order_images_size > MAX_ORDER_IMAGES_BYTES:
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Order total images size exceeds limit of 600 KB"
                        )

            # Validate input using CalculateRequest
            try:
                validated_request = CalculateRequest(**input_dict)
            except ValidationError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Item {idx} parameters validation failed"
                )

            # Validate dimensions limits
            if validated_request.width <= 0 or validated_request.width > 4000 or validated_request.height <= 0 or validated_request.height > 3000:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Габарити перевищують інженерні норми"
                )

            # Calculate trusted result with isolated item context
            validated_input_dict = validated_request.model_dump(exclude_unset=True)
            trusted_result = calc.calculate_project(
                validated_input_dict,
                item_pricing_context,
            )

            # Verify calculations invariants
            cbd = trusted_result.get("commercial_breakdown", {})
            if (
                trusted_result.get("net_price") != cbd.get("net_price") or
                trusted_result.get("vat_amount") != cbd.get("vat_amount") or
                trusted_result.get("cost_details", {}).get("total") != cbd.get("total")
            ):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Внутрішня помилка розрахунку ціни"
                )

            # Restore images for persisted input
            persisted_input = {**validated_input_dict}
            if images is not None:
                persisted_input["images"] = images

            trusted_items.append({
                "input": persisted_input,
                "result": trusted_result
            })

        # 4. Calculate true order-level commercial breakdown
        items_breakdowns = [item["result"]["commercial_breakdown"] for item in trusted_items]
        order_cb = calculate_order_commercials(items_breakdowns, pricing_context)

        grand_net = order_cb["net_price"]
        grand_vat = order_cb["vat_amount"]
        grand_total = order_cb["total"]


        # 5. Build sanitized document
        order_id = str(uuid.uuid4())[:8].upper()
        if USE_FIRESTORE:
            order_record = {
                "id": order_id,
                "timestamp": datetime.now(),
                "owner_uid": uid,
                "user_email": current_user.get("email"),
                "calculation_provenance": "server_calculated",
                "cart": {
                    "items": trusted_items
                },
                "order_commercial_breakdown": order_cb,
                "grand_net": grand_net,
                "grand_vat": grand_vat,
                "grand_total": grand_total
            }
            try:
                calc.db.collection('orders').document(order_id).set(order_record)
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Internal Server Error"
                )


        return {"status": "success", "order_id": order_id}

    except HTTPException:
        raise
    except InvalidUIDError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication context is invalid",
        ) from exc
    except UserSettingsNotReadableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User settings are temporarily unavailable",
        ) from exc
    except UserSettingsInvalidDocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored user settings are invalid",
        ) from exc
    except MissingResolvedPriceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Внутрішня помилка розрахунку ціни",
        ) from exc
    except UnknownMaterialError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Невідомий матеріал або конфігурація",
        ) from exc
    except CalculatorPricingError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Помилка конфігурації калькулятора",
        ) from exc
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal Server Error"
        )

@app.get("/api/generate-quote/{order_id}")
async def get_quote_pdf(order_id: str, current_user: dict = Depends(verify_firebase_token)):
    if not USE_FIRESTORE:
        raise HTTPException(status_code=400, detail="Firestore is required for history-based PDF")

    try:
        uid = get_authenticated_uid(current_user)
        data = get_owned_order_or_404(order_id, uid)

        if "cart" in data:
            cart_data = data["cart"]
            cart_data["order_id"] = order_id
            if "order_commercial_breakdown" in data:
                cart_data["order_commercial_breakdown"] = data["order_commercial_breakdown"]
        else:
            # Legacy fallback for single-window orders
            cart_data = {
                "order_id": order_id,
                "items": [{"input": data.get("input", {}), "result": data.get("result", {})}]
            }

        try:
            pdf_content = generate_cart_pdf(cart_data)
        except Exception:
            raise HTTPException(status_code=500, detail="Internal Server Error")

        return Response(
            content=bytes(pdf_content),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=WindowApp_Quote_{order_id}.pdf"}
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal Server Error")

@app.get("/api/orders")
async def get_user_orders(current_user: dict = Depends(verify_firebase_token)):
    if not USE_FIRESTORE:
        return []
    try:
        uid = get_authenticated_uid(current_user)
        query = calc.db.collection('orders').where('owner_uid', '==', uid)
        docs = query.limit(20).stream()

        results = []
        for doc in docs:
            d = doc.to_dict()
            if d and isinstance(d, dict) and d.get("owner_uid") == uid:
                results.append(d)

        # Safe in-memory sorting that tolerates missing/invalid timestamps
        def safe_timestamp_key(order: dict):
            ts = order.get("timestamp")
            if isinstance(ts, datetime):
                if ts.tzinfo is not None:
                    ts = ts.replace(tzinfo=None)
                return (1, ts)
            elif isinstance(ts, str):
                try:
                    dt = datetime.fromisoformat(ts)
                    if dt.tzinfo is not None:
                        dt = dt.replace(tzinfo=None)
                    return (1, dt)
                except Exception:
                    pass
            return (0, datetime.min)

        results.sort(key=safe_timestamp_key, reverse=True)
        return results[:10]
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Service Unavailable")


def build_settings_response(
    stored: UserSettingsStored,
    *,
    is_default: bool,
) -> UserSettingsResponse:
    return UserSettingsResponse(
        **stored.model_dump(mode="python"),
        is_default=is_default,
    )

@app.get("/api/settings", response_model=UserSettingsResponse)
def get_settings(
    current_user: dict = Depends(verify_firebase_token),
    repo: UserSettingsRepository = Depends(get_settings_repo),
) -> UserSettingsResponse:
    uid = get_authenticated_uid(current_user)
    try:
        result = repo.get_user_settings(uid)
    except InvalidUIDError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication context is invalid",
        ) from exc
    except UserSettingsNotReadableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User settings are temporarily unavailable",
        ) from exc
    except UserSettingsInvalidDocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored user settings are invalid",
        ) from exc

    return build_settings_response(result.settings, is_default=result.is_default)

@app.put("/api/settings", response_model=UserSettingsResponse)
def put_settings(
    settings: UserSettingsUpdate,
    current_user: dict = Depends(verify_firebase_token),
    repo: UserSettingsRepository = Depends(get_settings_repo),
) -> UserSettingsResponse:
    uid = get_authenticated_uid(current_user)
    try:
        stored = repo.save_user_settings(uid, settings)
    except InvalidUIDError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication context is invalid",
        ) from exc
    except UserSettingsWriteError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to save user settings",
        ) from exc
    except UserSettingsInvalidDocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored user settings are invalid",
        ) from exc

    return build_settings_response(stored, is_default=False)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
