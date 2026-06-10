import json
import math
import os

try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False

class GeometryEngine:
    @staticmethod
    def calculate_arc_params(width, height):
        """
        Calculates arc parameters based on chord width and segment height.
        Returns radius, arc length, and segment area.
        """
        if height <= 0:
            return 0, 0, 0
        # Radius of the circle passing through segment height and chord width
        # R = (h/2) + (w^2 / 8h)
        radius = (height / 2) + (width**2 / (8 * height))
        
        # Central angle in radians: theta = 2 * arcsin(w / 2R)
        # Handle edge case where width > 2R (math error)
        sine_val = width / (2 * radius)
        if sine_val > 1.0: sine_val = 1.0
        angle = 2 * math.asin(sine_val)
        
        arc_length = radius * angle
        # Segment area: A = (R^2 / 2) * (theta - sin(theta))
        segment_area = (radius**2 / 2) * (angle - math.sin(angle))
        
        return radius, arc_length, segment_area

class WindowCalculator:
    def __init__(self, materials_path="materials.json", taxes_path="tax_profiles.json", use_firestore=False):
        self.materials_path = materials_path
        self.taxes_path = taxes_path
        self.materials: dict = {}
        self.taxes: dict = {}
        self.use_firestore = use_firestore
        
        if use_firestore and FIREBASE_AVAILABLE:
            self._init_firestore()
            self._load_from_firestore()
        else:
            self._load_materials_local()
            self._load_taxes_local()

    def _init_firestore(self):
        """Initializes Firebase Admin SDK for Firestore access"""
        try:
            # 1. Try to load from environment variable (JSON string) - for Cloud Run
            json_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
            
            if json_creds:
                try:
                    cred_dict = json.loads(json_creds)
                    cred = credentials.Certificate(cred_dict)
                except Exception as e:
                    print(f"Error parsing GOOGLE_APPLICATION_CREDENTIALS_JSON: {e}")
                    # Fallback to local file if JSON is invalid
                    cred = credentials.Certificate("service-account.json")
            else:
                # 2. Fallback to local file (for local development)
                cred = credentials.Certificate("service-account.json")
                
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
            self.db = firestore.client()
            print("✅ Firestore initialized successfully")
        except Exception as e:
            print(f"❌ Firestore Initialization Error: {e}")
            self.db = None

    def _load_from_firestore(self):
        try:
            # Load profiles
            profiles_ref = self.db.collection('materials').document('profiles').get()
            fillings_ref = self.db.collection('materials').document('fillings').get()
            hardware_ref = self.db.collection('materials').document('hardware').get()
            extras_ref = self.db.collection('materials').document('extras').get()
            colors_ref = self.db.collection('materials').document('colors').get()
            
            self.materials = {
                "profiles": profiles_ref.to_dict() or {},
                "fillings": fillings_ref.to_dict() or {},
                "hardware": hardware_ref.to_dict() or {},
                "extras": extras_ref.to_dict() or {},
                "colors": colors_ref.to_dict() or {}
            }
            
            taxes_ref = self.db.collection('settings').document('taxes').get()
            self.taxes = taxes_ref.to_dict() or {"no_tax": {"name": "Без податку", "rate": 0.0, "legal_reference": ""}}
        except Exception as e:
            print(f"Firestore load error: {e}. Falling back to local.")
            self._load_materials_local()
            self._load_taxes_local()

    def _load_materials_local(self):
        try:
            with open(self.materials_path, 'r', encoding='utf-8') as f:
                self.materials = json.load(f)
        except Exception as e:
            self.materials = {"profiles": {}, "fillings": {}, "hardware": {}, "extras": {}, "colors": {}}
            print(f"Warning: Could not load materials.json - {e}")

    def _load_taxes_local(self):
        try:
            with open(self.taxes_path, 'r', encoding='utf-8') as f:
                self.taxes = json.load(f)
        except Exception as e:
            self.taxes = {"no_tax": {"name": "Без податку", "rate": 0.0, "legal_reference": ""}}
            print(f"Warning: Could not load tax_profiles.json - {e}")

    def validate_order(self, payload: dict) -> dict:
        width = payload.get("width", 1000.0)
        height = payload.get("height", 1000.0)
        profile_key = payload.get("profile", "REHAU_Euro_70")
        glass_key = payload.get("glass", "glass_24")
        panels = payload.get("panels", [{"type": "fixed", "proportion": 100.0}])

        prof_data = self.materials.get("profiles", {}).get(profile_key, {})
        glass_data = self.materials.get("fillings", {}).get(glass_key, {})
        limits = prof_data.get("limits", {})
        
        if not limits:
            return {"valid": True, "messages": []}
            
        max_w = limits.get("max_width", 1300)
        max_h = limits.get("max_height", 1400)
        max_wt = limits.get("max_weight", 80)
        
        messages = []
        is_valid = True
        
        for idx, panel in enumerate(panels):
            ptype = panel.get("type", "fixed")
            if ptype in ["turn", "tilt_turn", "door", "turn_right", "turn_left", "tilt_turn_right", "tilt_turn_left"]:
                prop = float(panel.get("proportion", 100)) / 100.0
                panel_w = width * prop
                panel_h = height # Simplified for 1-tier
                
                glass_w = max(0.0, panel_w - 120.0)
                glass_h = max(0.0, panel_h - 120.0)
                glass_area_m2 = (glass_w * glass_h) / 1_000_000.0
                
                sash_perimeter_m = (panel_w + panel_h) * 2 / 1000.0
                prof_weight = prof_data.get("weight_per_m", 1.2)
                glass_weight = glass_data.get("weight_per_m2", 20.0)
                
                panel_weight = (sash_perimeter_m * prof_weight) + (glass_area_m2 * glass_weight)
                
                issues = []
                if panel_w > max_w:
                    issues.append(f"ширина {round(panel_w,1)}мм > {max_w}мм")
                if panel_h > max_h:
                    issues.append(f"висота {round(panel_h,1)}мм > {max_h}мм")
                if panel_weight > max_wt:
                    issues.append(f"вага {round(panel_weight, 1)}кг > {max_wt}кг")
                    
                if issues:
                    is_valid = False
                    msg = f"Стулка №{idx+1} ({ptype}) не проходить за лімітами профілю {prof_data.get('name', profile_key)}. Порушення: {', '.join(issues)}."
                    messages.append(msg)
                    
        return {"valid": is_valid, "messages": messages}

    def calculate_project(self, payload: dict) -> dict:
        validation = self.validate_order(payload)
        if not validation["valid"]:
            return {"status": "error", "message": "\n".join(validation["messages"])}

        width = payload.get("width", 1000.0)
        height = payload.get("height", 1000.0)
        is_arched = payload.get("type") == "arched"
        arc_height = payload.get("arc_height", width / 2) if is_arched else 0
        
        profile_key = payload.get("profile", "REHAU_Euro_70")
        glass_key = payload.get("glass", "glass_24")
        color_key = payload.get("color", "white")
        panels = payload.get("panels", [{"type": "fixed", "proportion": 100.0}])
        v_sections = len(panels)
        
        custom_prices = payload.get("custom_prices", {})

        prof_data = self.materials.get("profiles", {}).get(profile_key, {})
        glass_data = self.materials.get("fillings", {}).get(glass_key, {})
        color_data = self.materials.get("colors", {}).get(color_key, {})
        
        is_aluminum = prof_data.get("material_type") == "aluminum"

        def _get_price(val, default):
            try:
                if val is not None and str(val).strip() != "":
                    return float(val)
                return default
            except:
                return default

        prof_price = _get_price(custom_prices.get("profile_price"), prof_data.get("price_per_m", 200.0))
        glass_price = _get_price(custom_prices.get("glass_price"), glass_data.get("price_per_m2", 800.0))

        # Geometry Logic
        frame_perimeter = (width + height) * 2
        glass_area_total = (width * height) / 1_000_000.0 # simplified base
        
        bending_cost = 0.0
        if is_arched:
            radius, arc_len, segment_area = GeometryEngine.calculate_arc_params(width, arc_height)
            # Frame perimeter for arch: replace top width with arc length
            frame_perimeter = frame_perimeter - width + arc_len
            # Glass area adjustment
            rect_area = (width * (height - arc_height)) / 1_000_000.0
            glass_area_total = rect_area + (segment_area / 1_000_000.0)
            
            # Bending extra cost
            base_bending = self.materials.get("extras", {}).get("bending", {}).get("price_per_m", 600.0)
            bend_multiplier = 2.5 if is_aluminum else 1.5
            bending_cost = (arc_len / 1000.0) * base_bending * bend_multiplier

        v_mullions_count = max(0, v_sections - 1)
        v_mullions_len = v_mullions_count * (height - (arc_height/2 if is_arched else 0))
        
        total_profile_mm = frame_perimeter + v_mullions_len
        
        hardware_cost = 0.0
        mosquito_nets_cost = 0.0
        hardware_list = []

        for panel in panels:
            prop = float(panel.get("proportion", 100)) / 100.0
            panel_w = width * prop
            ptype = panel.get("type", "fixed")
            
            if ptype != "fixed":
                # Normalize type for hardware matching
                base_hw = "tilt_turn" if "tilt_turn" in ptype else "turn"
                if "door" in ptype: base_hw = "door_lock_strip"
                
                # Try to get from materials
                hw_data = self.materials.get("hardware", {}).get(base_hw)
                if not hw_data:
                    # Fallback to general 'hardware' entry or hardcoded default
                    hw_data = self.materials.get("hardware", {}).get("standard", {"price": 650.0})
                
                hardware_list.append(hw_data.get("name", base_hw))
                hardware_cost += _get_price(custom_prices.get("hardware_price"), hw_data.get("price", 650.0))

            if panel.get("mosquito", False):
                net_price = self.materials.get("extras", {}).get("mosquito_net", {}).get("price_per_m2", 400.0)
                # For arched windows, net area is usually the rectangular part + half arch
                net_h = height - (arc_height/2 if is_arched else 0)
                net_area = (panel_w * net_h) / 1_000_000.0
                mosquito_nets_cost += net_area * net_price

        # Extras: Sill and Board
        sill_len = payload.get("sill_length", 0.0)
        sill_width = payload.get("sill_width", 0.0)
        sill_cost = 0.0
        if sill_len > 0 and sill_width > 0:
            sill_data = self.materials.get("extras", {}).get("sill", {})
            sill_cost = (sill_len * sill_width / 1_000_000.0) * sill_data.get("price_per_m2", 600.0)

        board_type = payload.get("window_board", "none")
        board_len = payload.get("window_board_length", 0.0)
        board_depth = payload.get("window_board_depth", 0.0)
        board_cost = 0.0
        if board_type != "none" and board_len > 0:
            board_data = self.materials.get("extras", {}).get(board_type, {})
            board_cost = (board_len * board_depth / 1_000_000.0) * board_data.get("price_per_m2", 0.0)

        color_multiplier = color_data.get("price_multiplier", 1.0)
        profile_total_cost = (total_profile_mm / 1000.0) * prof_price * color_multiplier
        glass_total_cost = glass_area_total * glass_price
        
        subtotal = profile_total_cost + glass_total_cost + hardware_cost + mosquito_nets_cost + sill_cost + board_cost + bending_cost
        
        tax_id = payload.get("tax_profile_id", "no_tax")
        tax_profile = self.taxes.get(tax_id, self.taxes.get("no_tax", {}))
        tax_rate = tax_profile.get("rate", 0.0)
        vat_amount = subtotal * tax_rate
        final_total = subtotal + vat_amount

        # Calculate technical metrics
        prof_weight_per_m = prof_data.get("weight_per_m", 1.2)
        glass_weight_per_m2 = glass_data.get("weight_per_m2", 20.0)
        
        total_weight = (total_profile_mm / 1000.0) * prof_weight_per_m + (glass_area_total * glass_weight_per_m2)
        
        # Hardware adds a little bit of weight (approx 1kg per sash)
        hardware_weight = sum([1.5 for p in panels if p.get("type", "fixed") != "fixed"])
        total_weight += hardware_weight

        return {
            "status": "success",
            "net_price": round(subtotal, 2),
            "vat_amount": round(vat_amount, 2),
            "legal_reference": tax_profile.get("legal_reference", ""),
            "metrics": {
                "area": round(glass_area_total, 4),
                "perimeter": round(frame_perimeter / 1000.0, 2),
                "weight": round(total_weight, 2)
            },
            "cost_details": {
                "profile": round(profile_total_cost, 2),
                "glass": round(glass_total_cost, 2),
                "hardware": round(hardware_cost, 2),
                "extras": round(mosquito_nets_cost + sill_cost + board_cost + bending_cost, 2),
                "total": round(final_total, 2)
            }
        }
