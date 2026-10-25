import time

from config import (
    PROXIMITY_WARNING_CM,
    PROXIMITY_CRITICAL_CM,
    MIN_VALID_DISTANCE_CM,
    MAX_VALID_DISTANCE_CM,
    PROXIMITY_VOICE_COOLDOWN,
)


class ProximitySafetyManager:
    """
    Processes ultrasonic distance readings.

    States:
        SAFE
        WARNING
        CRITICAL
        INVALID

    This module is independent of YOLO detection.
    """

    def __init__(self):
        self.current_state = "INVALID"
        self.last_distance_cm = None
        self.last_voice_time = 0.0
        self.last_announced_state = None

    def classify_distance(self, distance_cm):
        """
        Convert a distance reading into a safety state.
        """

        if distance_cm is None:
            return "INVALID"

        try:
            distance_cm = float(distance_cm)
        except (TypeError, ValueError):
            return "INVALID"

        if not (
            MIN_VALID_DISTANCE_CM
            <= distance_cm
            <= MAX_VALID_DISTANCE_CM
        ):
            return "INVALID"

        if distance_cm <= PROXIMITY_CRITICAL_CM:
            return "CRITICAL"

        if distance_cm <= PROXIMITY_WARNING_CM:
            return "WARNING"

        return "SAFE"

    def update(self, distance_cm):
        """
        Process a new distance reading.

        Returns a dictionary describing the current safety state,
        plus whether a voice warning should be generated.
        """

        now = time.time()

        state = self.classify_distance(distance_cm)

        self.current_state = state
        self.last_distance_cm = distance_cm

        should_announce = False
        message = None

        # ---------------------------------------------------------
        # CRITICAL
        # ---------------------------------------------------------

        if state == "CRITICAL":

            message = "Caution! Obstacle very close ahead."

            if (
                self.last_announced_state != "CRITICAL"
                or now - self.last_voice_time
                >= PROXIMITY_VOICE_COOLDOWN
            ):
                should_announce = True

        # ---------------------------------------------------------
        # WARNING
        # ---------------------------------------------------------

        elif state == "WARNING":

            message = "Caution! Obstacle close ahead."

            if (
                self.last_announced_state != "WARNING"
                or now - self.last_voice_time
                >= PROXIMITY_VOICE_COOLDOWN
            ):
                should_announce = True

        # ---------------------------------------------------------
        # SAFE
        # ---------------------------------------------------------

        elif state == "SAFE":

            message = None

        # ---------------------------------------------------------
        # INVALID
        # ---------------------------------------------------------

        elif state == "INVALID":

            message = None

        # ---------------------------------------------------------
        # Record announcement
        # ---------------------------------------------------------

        if should_announce:
            self.last_voice_time = now
            self.last_announced_state = state

        return {
            "state": state,
            "distance_cm": distance_cm,
            "should_announce": should_announce,
            "message": message,
        }

    def get_status(self):
        """
        Return the latest proximity status.
        """

        return {
            "state": self.current_state,
            "distance_cm": self.last_distance_cm,
        }

    def reset(self):
        """Reset proximity state."""

        self.current_state = "INVALID"
        self.last_distance_cm = None
        self.last_voice_time = 0.0
        self.last_announced_state = None