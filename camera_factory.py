from laptop_camera import LaptopCamera
from esp32_camera import ESP32Camera


def create_camera(source="laptop"):
    source = str(source).strip().lower()

    if source == "laptop":
        return LaptopCamera()

    if source == "esp32":
        return ESP32Camera()

    raise ValueError(
        f"Unknown camera source: {source}. "
        "Use 'laptop' or 'esp32'."
    )
