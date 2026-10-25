import time


class DetectionStateManager:
    """
    Tracks detected objects across frames.

    The manager does NOT perform YOLO detection.
    It only compares the current detections with the previous state.

    Main events:
        NEW       -> object appeared
        PRESENT   -> object is still present
        DISAPPEARED -> object is no longer detected
    """

    def __init__(self, disappear_delay=1.0):
        self.disappear_delay = disappear_delay

        # Current tracked state:
        # {
        #     "object_name": {
        #         "last_seen": timestamp,
        #         "zone": "CENTER",
        #         "confidence": 0.75,
        #     }
        # }
        self.objects = {}

    def update(self, detections):
        """
        Update the state using the latest YOLO detections.

        Args:
            detections: list of detection dictionaries from YOLODetector.

        Returns:
            List of events generated during this update.
        """

        now = time.time()
        events = []

        currently_detected = set()

        # ---------------------------------------------------------
        # Process currently visible objects
        # ---------------------------------------------------------

        for detection in detections:

            object_name = detection["class_name"]
            zone = detection["zone"]
            confidence = detection["confidence"]

            currently_detected.add(object_name)

            if object_name not in self.objects:

                # Object has appeared for the first time.
                self.objects[object_name] = {
                    "last_seen": now,
                    "zone": zone,
                    "confidence": confidence,
                }

                events.append(
                    {
                        "event": "NEW",
                        "class_name": object_name,
                        "zone": zone,
                        "confidence": confidence,
                    }
                )

            else:

                # Object is still visible.
                self.objects[object_name]["last_seen"] = now
                self.objects[object_name]["zone"] = zone
                self.objects[object_name]["confidence"] = confidence

                events.append(
                    {
                        "event": "PRESENT",
                        "class_name": object_name,
                        "zone": zone,
                        "confidence": confidence,
                    }
                )

        # ---------------------------------------------------------
        # Detect objects that disappeared
        # ---------------------------------------------------------

        disappeared_objects = []

        for object_name, state in self.objects.items():

            if object_name in currently_detected:
                continue

            time_since_seen = now - state["last_seen"]

            if time_since_seen >= self.disappear_delay:

                events.append(
                    {
                        "event": "DISAPPEARED",
                        "class_name": object_name,
                        "zone": state["zone"],
                        "confidence": state["confidence"],
                    }
                )

                disappeared_objects.append(object_name)

        # Remove disappeared objects from the active state.
        for object_name in disappeared_objects:
            del self.objects[object_name]

        return events

    def get_active_objects(self):
        """
        Return currently tracked objects.

        Useful later for the dashboard.
        """

        return self.objects.copy()

    def reset(self):
        """Clear all tracked objects."""

        self.objects.clear()