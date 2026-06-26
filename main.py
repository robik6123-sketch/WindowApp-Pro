from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from calculator import (
    WindowCalculator,
    CalculatorPricingError,
    UnknownMaterialError,
    MissingResolvedPriceError
)
from pricing_context_provider import get_default_pricing_context
from pdf_generator import generate_cart_pdf
import os
import json
import uuid
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
from settings_models import UserSettingsResponse, UserSettingsUpdate, UserSettingsStored, BusinessFloat
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator

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

async def get_current_user(res: HTTPAuthorizationCredentials = Depends(security)):
    """Verifies Firebase ID Token"""
    try:
        decoded_token = auth.verify_id_token(res.credentials)
        return decoded_token
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")

@app.get("/")
async def root():
    """Serves the main frontend interface"""
    return FileResponse("index.html")

@app.post("/api/calculate")
async def calculate(order: CalculateRequest, current_user: dict = Depends(verify_firebase_token)):
    try:
        if order.width > 4000 or order.height > 3000:
            raise HTTPException(status_code=400, detail="Габарити перевищують інженерні норми")

        order_dict = order.model_dump(exclude_unset=True)
        pricing_context = get_default_pricing_context(calc.materials)
        result = calc.calculate_project(order_dict, pricing_context)
        # We don't save to firestore here anymore, because this is just a calculation preview.
        # Saving happens in /api/create-order
        return result
    except MissingResolvedPriceError as e:
        raise HTTPException(status_code=500, detail="Внутрішня помилка розрахунку ціни")
    except UnknownMaterialError as e:
        raise HTTPException(status_code=400, detail="Невідомий матеріал або конфігурація")
    except CalculatorPricingError as e:
        raise HTTPException(status_code=500, detail="Помилка конфігурації калькулятора")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/create-order")
async def create_order(cart: dict, current_user: dict = Depends(verify_firebase_token)):
    try:
        order_id = str(uuid.uuid4())[:8].upper()
        if USE_FIRESTORE:
            order_record = {
                "id": order_id,
                "timestamp": datetime.now(),
                "owner_uid": current_user["uid"],
                "user_email": current_user.get("email"),
                "cart": cart
            }
            calc.db.collection('orders').document(order_id).set(order_record)

        return {"status": "success", "order_id": order_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal Server Error")

@app.get("/api/generate-quote/{order_id}")
async def get_quote_pdf(order_id: str, current_user: dict = Depends(verify_firebase_token)):
    if not USE_FIRESTORE:
        raise HTTPException(status_code=400, detail="Firestore is required for history-based PDF")

    try:
        try:
            doc_ref = calc.db.collection('orders').document(order_id).get()
        except Exception:
            raise HTTPException(status_code=500, detail="Internal Server Error")

        if not doc_ref.exists:
            raise HTTPException(status_code=404, detail="Замовлення не знайдено")

        data = doc_ref.to_dict()

        if data.get("owner_uid") != current_user["uid"]:
            raise HTTPException(status_code=403, detail="Forbidden")

        if "cart" in data:
            cart_data = data["cart"]
            cart_data["order_id"] = order_id
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
        query = calc.db.collection('orders').where('owner_uid', '==', current_user["uid"])
        docs = query.limit(20).stream()
        results = [doc.to_dict() for doc in docs]
        results.sort(key=lambda x: x.get('timestamp'), reverse=True)
        return results[:10]
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Service Unavailable")

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
