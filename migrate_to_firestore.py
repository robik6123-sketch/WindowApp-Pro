import firebase_admin
from firebase_admin import credentials, firestore
import json
import os

# Path to service account key
cred_path = "service-account.json"

if not os.path.exists(cred_path):
    print("Error: service-account.json not found!")
    exit(1)

cred = credentials.Certificate(cred_path)
firebase_admin.initialize_app(cred)
db = firestore.client()

def migrate():
    print("🚀 Starting migration to Firestore...")
    
    # 1. Migrate Materials
    try:
        with open("materials.json", "r", encoding="utf-8") as f:
            materials = json.load(f)
            
        for category in ["profiles", "fillings", "hardware", "extras", "colors"]:
            if category in materials:
                print(f"  - Syncing {category}...")
                db.collection('materials').document(category).set(materials[category])
        
        # 2. Migrate Taxes
        with open("tax_profiles.json", "r", encoding="utf-8") as f:
            taxes = json.load(f)
            print("  - Syncing taxes...")
            db.collection('settings').document('taxes').set(taxes)
            
        print("✅ Migration completed successfully!")
    except Exception as e:
        print(f"❌ Migration failed: {e}")

if __name__ == "__main__":
    migrate()
