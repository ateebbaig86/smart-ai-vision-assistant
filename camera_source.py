from abc import ABC, abstractmethod


class CameraSource(ABC):

    @abstractmethod
    def connect(self):
        """Connect to the camera source."""
        pass

    @abstractmethod
    def read(self):
        """Return the latest frame, or None if unavailable."""
        pass

    @abstractmethod
    def release(self):
        """Release the camera source."""
        pass

    @property
    @abstractmethod
    def is_connected(self):
        """Return True when the camera source is connected."""
        pass
