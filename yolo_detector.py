from pathlib import Path

from ultralytics import YOLO

from config import (
    YOLO_MODEL,
    YOLO_CONFIDENCE,
    YOLO_IMAGE_SIZE,
    LEFT_ZONE_END,
    CENTER_ZONE_END,
)


class YOLODetector:
    """
    Unified YOLO26n detection wrapper.

    Responsibilities:
    - Load the existing Unified YOLO26n model.
    - Run inference on an image/frame.
    - Convert detections into simple Python dictionaries.
    - Determine whether an object is LEFT, CENTER, or RIGHT.
    """

    def __init__(self):
        model_path = Path(YOLO_MODEL)

        if not model_path.exists():
            raise FileNotFoundError(
                f"Unified YOLO model not found:\n{model_path}"
            )

        print(f"[YOLO] Loading model: {model_path}")

        self.model = YOLO(str(model_path))

        print(f"[YOLO] Model loaded successfully.")
        print(f"[YOLO] Classes: {len(self.model.names)}")

    def get_zone(self, center_x, frame_width):
        """
        Determine horizontal position of an object.

        Returns:
            LEFT, CENTER, or RIGHT
        """

        if frame_width <= 0:
            return "CENTER"

        normalized_x = center_x / frame_width

        if normalized_x < LEFT_ZONE_END:
            return "LEFT"

        if normalized_x < CENTER_ZONE_END:
            return "CENTER"

        return "RIGHT"

    def detect(self, frame):
        """
        Run YOLO detection on one OpenCV BGR frame.

        Returns:
            List of dictionaries containing:
            {
                "class_id": int,
                "class_name": str,
                "confidence": float,
                "bbox": (x1, y1, x2, y2),
                "center": (cx, cy),
                "zone": str
            }
        """

        if frame is None:
            return []

        frame_height, frame_width = frame.shape[:2]

        results = self.model.predict(
            source=frame,
            conf=YOLO_CONFIDENCE,
            imgsz=YOLO_IMAGE_SIZE,
            verbose=False,
        )

        detections = []

        for result in results:

            if result.boxes is None:
                continue

            for box in result.boxes:

                class_id = int(box.cls[0])
                confidence = float(box.conf[0])

                x1, y1, x2, y2 = box.xyxy[0].tolist()

                x1 = int(x1)
                y1 = int(y1)
                x2 = int(x2)
                y2 = int(y2)

                center_x = int((x1 + x2) / 2)
                center_y = int((y1 + y2) / 2)

                class_name = self.model.names[class_id]

                zone = self.get_zone(
                    center_x,
                    frame_width,
                )

                detections.append(
                    {
                        "class_id": class_id,
                        "class_name": class_name,
                        "confidence": confidence,
                        "bbox": (x1, y1, x2, y2),
                        "center": (center_x, center_y),
                        "zone": zone,
                    }
                )

        return detections