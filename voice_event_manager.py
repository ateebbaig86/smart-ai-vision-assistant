import time
from config import OBJECT_VOICE_COOLDOWN


class VoiceEventManager:
    """
    Controls which detection/recognition events should be announced.

    Object policy:
        - Announce a newly detected object.
        - Prevent repeated announcements using the object cooldown.

    Person recognition policy:
        - Announce a recognized person when first seen.
        - Do not repeatedly announce the same person while continuously visible.
        - A different recognized person can be announced immediately.
        - The same person can be announced again after disappearing.
        - Unknown persons are not announced.
    """

    def __init__(self):
        self.object_last_spoken = {}
        self.active_people = set()

    def object_message(self, detection):
        class_name = detection.get("class_name", "object")
        zone = detection.get("zone", "CENTER")
        spoken_name = class_name.replace("_", " ")
        return f"{spoken_name} {zone.lower()}."

    def should_speak_object(self, detection):
        class_name = detection.get("class_name", "object")
        now = time.time()
        last_time = self.object_last_spoken.get(class_name, 0.0)

        if now - last_time >= OBJECT_VOICE_COOLDOWN:
            self.object_last_spoken[class_name] = now
            return True

        return False

    def person_message(self, person_name):
        return f"{person_name} is here."

    def should_speak_person(self, person_name):
        """Announce a person only when they are newly recognized."""
        if not person_name or person_name in self.active_people:
            return False

        self.active_people.add(person_name)
        return True

    def process_object_event(self, event):
        if not event or event.get("event") != "NEW":
            return None

        if not self.should_speak_object(event):
            return None

        return {
            "type": "OBJECT",
            "priority": 2,
            "message": self.object_message(event),
        }

    def process_person_event(self, person_name):
        if not person_name or not self.should_speak_person(person_name):
            return None

        return {
            "type": "PERSON",
            # Person announcements should jump ahead of pending object speech.
            "priority": 1,
            "message": self.person_message(person_name),
        }

    def person_disappeared(self, person_name):
        if person_name:
            self.active_people.discard(person_name)

    def update_active_people(self, recognized_people):
        current_people = {name for name in recognized_people if name}
        disappeared_people = self.active_people - current_people
        for person_name in disappeared_people:
            self.active_people.discard(person_name)

    def reset(self):
        self.object_last_spoken.clear()
        self.active_people.clear()
