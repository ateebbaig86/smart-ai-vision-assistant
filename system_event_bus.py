import queue
import threading
from datetime import datetime


class SystemEventBus:

    def __init__(self, max_events=500):
        self.max_events = max_events

        self._events = queue.Queue(
            maxsize=max_events
        )

        self._history = []

        self._lock = threading.Lock()

        print(
            "[EVENT BUS] Real-time event bus initialized."
        )

    def publish(
        self,
        level,
        message,
        category="SYSTEM"
    ):
        event = {
            "timestamp": datetime.now().strftime(
                "%H:%M:%S"
            ),
            "level": str(level).upper(),
            "category": str(category).upper(),
            "message": str(message)
        }

        with self._lock:

            self._history.append(event)

            if len(self._history) > self.max_events:
                self._history.pop(0)

        try:
            self._events.put_nowait(event)

        except queue.Full:
            try:
                self._events.get_nowait()
            except queue.Empty:
                pass

            try:
                self._events.put_nowait(event)
            except queue.Full:
                pass

        return event

    def get_event(self, timeout=None):
        try:
            return self._events.get(
                timeout=timeout
            )
        except queue.Empty:
            return None

    def get_history(self, limit=None):

        with self._lock:

            if limit is None:
                return list(self._history)

            return list(
                self._history[-int(limit):]
            )

    def clear(self):

        with self._lock:
            self._history.clear()

        while True:
            try:
                self._events.get_nowait()
            except queue.Empty:
                break


system_event_bus = SystemEventBus()
