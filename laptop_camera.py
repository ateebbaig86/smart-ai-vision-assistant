import cv2

from camera_source import CameraSource


class LaptopCamera(CameraSource):

    def __init__(self, camera_index=0):

        self.camera_index = camera_index
        self.cap = None
        self.connected = False

        # Automatically connect when the camera object
        # is created.
        self.connect()

    def connect(self):

        print(
            f"[LAPTOP CAMERA] Connecting to camera "
            f"index {self.camera_index}..."
        )

        # Release any previous camera handle first.
        if self.cap is not None:

            self.cap.release()

            self.cap = None

        # Open laptop webcam using DirectShow on Windows.
        self.cap = cv2.VideoCapture(
            self.camera_index,
            cv2.CAP_DSHOW
        )

        if not self.cap.isOpened():

            self.connected = False

            print(
                "[LAPTOP CAMERA] Connection failed."
            )

            return False

        # Keep the capture buffer small to reduce latency.
        self.cap.set(
            cv2.CAP_PROP_BUFFERSIZE,
            1
        )

        self.connected = True

        print(
            "[LAPTOP CAMERA] Connected successfully."
        )

        return True

    def read(self):

        if self.cap is None or not self.connected:

            return None

        success, frame = self.cap.read()

        if not success or frame is None:

            self.connected = False

            print(
                "[LAPTOP CAMERA] Frame read failed."
            )

            return None

        return frame

    def release(self):

        if self.cap is not None:

            self.cap.release()

        self.cap = None
        self.connected = False

        print(
            "[LAPTOP CAMERA] Camera released."
        )

    @property
    def is_connected(self):

        return self.connected