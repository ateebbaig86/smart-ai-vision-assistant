import cv2
import time
import urllib.request
import urllib.error
import threading
import numpy as np

from config import ESP32_STREAM_URL


class ESP32Camera:
    """
    ESP32-CAM MJPEG stream client.

    Reads the HTTP MJPEG stream directly instead of using
    cv2.VideoCapture(), which failed with this ESP32 endpoint.

    Preserves the existing camera interface:
        connect()
        read()
        reconnect()
        release()
        is_connected
    """

    def __init__(self, stream_url=None):
        self.stream_url = stream_url or ESP32_STREAM_URL

        self.connected = False
        self._response = None
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

        self._reader_thread = None
        self._latest_frame = None
        self._last_frame_time = 0.0
        self._error = None

    def connect(self):
        """Open the ESP32 HTTP stream and start the frame reader."""

        self.release()

        print(f"[ESP32-CAM] Connecting to: {self.stream_url}")

        request = urllib.request.Request(
            self.stream_url,
            headers={
                "User-Agent": "ESP32-Vision-Assistant/1.0",
                "Accept": "multipart/x-mixed-replace",
                "Connection": "keep-alive",
            },
        )

        try:
            # Longer timeout reduces false disconnects on a busy hotspot.
            response = urllib.request.urlopen(
                request,
                timeout=30,
            )

            if response.status != 200:
                response.close()
                print(
                    f"[ESP32-CAM] Unexpected HTTP status: "
                    f"{response.status}"
                )
                return False

            content_type = response.headers.get(
                "Content-Type", ""
            ).lower()

            if "multipart/x-mixed-replace" not in content_type:
                response.close()
                print(
                    "[ESP32-CAM] Unexpected response content type: "
                    f"{content_type}"
                )
                return False

            self._response = response
            self._buffer = bytearray()
            self._stop_event.clear()
            self._error = None

            self._reader_thread = threading.Thread(
                target=self._reader_loop,
                name="ESP32-MJPEG-Reader",
                daemon=True,
            )

            self._reader_thread.start()

            print("[ESP32-CAM] HTTP stream opened successfully.")

            return True

        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.connected = False
            print(f"[ESP32-CAM] Connection failed: {exc}")
            return False

    def _reader_loop(self):
        """Continuously receive and decode JPEG frames."""

        try:
            self.connected = True

            while not self._stop_event.is_set():
                # read1() returns currently available socket data without
                # waiting for the requested buffer to fill on a live MJPEG stream.
                reader = getattr(self._response, "read1", self._response.read)
                chunk = reader(4096)

                if not chunk:
                    raise ConnectionError(
                        "ESP32 stream ended unexpectedly."
                    )

                self._buffer.extend(chunk)

                while not self._stop_event.is_set():
                    start = self._buffer.find(b"\xff\xd8")

                    if start < 0:
                        # Retain a possible partial JPEG marker.
                        if self._buffer:
                            self._buffer = self._buffer[-1:]
                        break

                    end = self._buffer.find(
                        b"\xff\xd9",
                        start + 2,
                    )

                    if end < 0:
                        # Discard bytes preceding the JPEG start.
                        if start > 0:
                            del self._buffer[:start]
                        break

                    jpeg_bytes = bytes(
                        self._buffer[start:end + 2]
                    )

                    del self._buffer[:end + 2]

                    encoded = np.frombuffer(
                        jpeg_bytes,
                        dtype=np.uint8,
                    )

                    frame = cv2.imdecode(
                        encoded,
                        cv2.IMREAD_COLOR,
                    )

                    if frame is None:
                        continue

                    with self._lock:
                        self._latest_frame = frame
                        self._last_frame_time = time.monotonic()

        except Exception as exc:
            if not self._stop_event.is_set():
                self._error = str(exc)
                print(
                    f"[ESP32-CAM] Stream reader stopped: {exc}"
                )

        finally:
            self.connected = False

    def read(self):
        """
        Return the latest available BGR frame.

        Returns None if no frame is currently available.
        """

        if not self.connected:
            return None

        with self._lock:
            if self._latest_frame is None:
                return None

            return self._latest_frame.copy()

    def reconnect(self, delay=2.0):
        """Release the current stream and reconnect."""

        self.release()

        print(
            f"[ESP32-CAM] Reconnecting in {delay:.1f} seconds..."
        )

        time.sleep(delay)

        return self.connect()

    def release(self):
        """Stop the reader and close the HTTP connection."""

        self._stop_event.set()
        self.connected = False

        response = self._response
        self._response = None

        if response is not None:
            try:
                response.close()
            except Exception:
                pass

        thread = self._reader_thread
        self._reader_thread = None

        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

        with self._lock:
            self._latest_frame = None
            self._last_frame_time = 0.0

        self._buffer = bytearray()

        print("[ESP32-CAM] Stream released.")

    @property
    def is_connected(self):
        """Whether the stream reader is currently connected."""

        return self.connected