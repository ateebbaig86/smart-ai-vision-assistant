from pathlib import Path


# ============================================================
# PROJECT PATHS
# ============================================================

# D:\AI_Project\New AI project\ESP32_Edge_System
BASE_DIR = Path(__file__).resolve().parent

# Existing Unified YOLO26n model
YOLO_MODEL = BASE_DIR / "Unified_Model_30ep.pt"


# ============================================================
# ESP32-CAM NETWORK SETTINGS
# ============================================================

# Replace this later with the ESP32-CAM's actual IP address.
ESP32_IP = "192.168.1.100"  # Replace with your ESP32-CAM IP

ESP32_STREAM_URL = f"http://{ESP32_IP}/stream"
ESP32_TELEMETRY_URL = f"http://{ESP32_IP}/telemetry"


# ============================================================
# CAMERA / YOLO SETTINGS
# ============================================================

YOLO_CONFIDENCE = 0.25
YOLO_IMAGE_SIZE = 640

# Camera display size expected from the ESP32-CAM.
FRAME_WIDTH = 640
FRAME_HEIGHT = 480


# ============================================================
# PROXIMITY SAFETY SETTINGS
# ============================================================

# Prototype starting thresholds.
# These will be calibrated with the real hardware later.

PROXIMITY_WARNING_CM = 40
PROXIMITY_CRITICAL_CM = 30

# Ignore obviously invalid ultrasonic readings.
MIN_VALID_DISTANCE_CM = 2
MAX_VALID_DISTANCE_CM = 400


# ============================================================
# VOICE SETTINGS
# ============================================================

OBJECT_VOICE_COOLDOWN = 4.0
PROXIMITY_VOICE_COOLDOWN = 1.0


# ============================================================
# SPATIAL ZONES
# ============================================================

# Frame divided into:
#
# LEFT       CENTER       RIGHT
#
# These values represent the horizontal boundaries.

LEFT_ZONE_END = 0.33
CENTER_ZONE_END = 0.66


# ============================================================
# DEBUG / DEVELOPMENT
# ============================================================

DEBUG = True

