from dataclasses import dataclass, field
import json

@dataclass
class Glass3D:
    width: float
    height: float
    glass_type: str
    thickness: float = 40.0
    x_offset: float = 0.0
    y_offset: float = 0.0
    z_offset: float = 0.0

@dataclass
class Sash3D:
    width: float
    height: float
    sash_type: str  # "fixed", "turn", "tilt-turn"
    opening_direction: str  # "left", "right", "none"
    profile_type: str
    glass: Glass3D
    x_offset: float = 0.0
    y_offset: float = 0.0
    z_offset: float = 0.0

@dataclass
class Frame3D:
    width: float
    height: float
    profile_type: str
    color: str
    v_sections: int
    h_sections: int
    sashes: list[Sash3D] = field(default_factory=list)

@dataclass
class Window3D:
    """
    Root object representing the entire window construction for 3D rendering.
    Can be serialized to JSON to pass to Three.js or similar engines.
    """
    id: str
    frame: Frame3D
    
    def to_dict(self):
        # A simple helper for serialization
        def _to_dict(obj):
            if hasattr(obj, "__dataclass_fields__"):
                return {k: _to_dict(getattr(obj, k)) for k in obj.__dataclass_fields__}
            elif isinstance(obj, list):
                return [_to_dict(item) for item in obj]
            else:
                return obj
        return _to_dict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

# Usage example:
# glass = Glass3D(width=500, height=1300, glass_type="glass_40")
# sash = Sash3D(width=580, height=1380, sash_type="turn", opening_direction="right", 
#               profile_type="WDS_500", glass=glass)
# frame = Frame3D(width=1200, height=1400, profile_type="WDS_500", color="#ffffff", 
#                 v_sections=2, h_sections=1, sashes=[sash])
# window = Window3D(id="win-001", frame=frame)
# print(window.to_json())
