from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from calculator import WindowCalculator
from pdf_generator import generate_cart_pdf
import os
import json
import uuid
from datetime import datetime
import firebase_admin
from firebase_admin import auth
from auth_dependency import verify_firebase_token

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
async def calculate(request: Request, order: dict):
    # Try to get user from token if provided (optional for calc)
    user_email = order.get("user_email")
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        try:
            token = auth_header.split(" ")[1]
            decoded = auth.verify_id_token(token)
            user_email = decoded.get("email")
        except: pass

    try:
        if order.get("width", 0) > 4000 or order.get("height", 0) > 3000:
            raise HTTPException(status_code=400, detail="Габарити перевищують інженерні норми")

        result = calc.calculate_project(order)
        # We don't save to firestore here anymore, because this is just a calculation preview.
        # Saving happens in /api/create-order
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/create-order")
async def create_order(request: Request, cart: dict):
    user_email = cart.get("user_email")
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        try:
            token = auth_header.split(" ")[1]
            decoded = auth.verify_id_token(token)
            user_email = decoded.get("email")
        except: pass

    try:
        order_id = str(uuid.uuid4())[:8].upper()
        if USE_FIRESTORE:
            order_record = {
                "id": order_id,
                "timestamp": datetime.now(),
                "user_email": user_email or "anonymous",
                "cart": cart
            }
            calc.db.collection('orders').document(order_id).set(order_record)

        return {"status": "success", "order_id": order_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/generate-quote/{order_id}")
async def get_quote_pdf(order_id: str):
    if not USE_FIRESTORE:
        raise HTTPException(status_code=400, detail="Firestore is required for history-based PDF")

    try:
        doc_ref = calc.db.collection('orders').document(order_id).get()
        if not doc_ref.exists:
            raise HTTPException(status_code=404, detail="Замовлення не знайдено")

        data = doc_ref.to_dict()

        if "cart" in data:
            cart_data = data["cart"]
            cart_data["order_id"] = order_id
        else:
            # Legacy fallback for single-window orders
            cart_data = {
                "order_id": order_id,
                "items": [{"input": data.get("input", {}), "result": data.get("result", {})}]
            }

        pdf_content = generate_cart_pdf(cart_data)
        return Response(
            content=bytes(pdf_content),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=WindowApp_Quote_{order_id}.pdf"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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

@app.post("/api/migrate")
async def migrate():
    """Endpoint to migrate local materials.json to Firestore"""
    if not USE_FIRESTORE:
        return {"status": "error", "message": "Firestore is not enabled"}

    try:
        with open("materials.json", "r", encoding="utf-8") as f:
            data = json.load(f)

        # Sync each category
        for category in ["profiles", "fillings", "hardware", "extras", "colors"]:
            if category in data:
                app.state.db.collection('materials').document(category).set(data[category])

        return {"status": "success", "message": "Data migrated to Firestore"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
