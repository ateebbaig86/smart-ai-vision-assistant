import time
import cv2
import hashlib
from esp32_camera import ESP32Camera

camera = ESP32Camera()

try:
    if not camera.connect():
        print("ERROR: Could not connect to ESP32-CAM.")
    else:
        print("Measuring unique frames for 15 seconds...")

        start = time.time()
        previous_hash = None
        unique_frames = 0
        total_reads = 0

        while time.time() - start < 15:
            frame = camera.read()

            if frame is None:
                time.sleep(0.005)
                continue

            total_reads += 1

            # Hash a resized grayscale image to detect image changes.
            small = cv2.resize(frame, (80, 60))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            current_hash = hashlib.md5(gray.tobytes()).hexdigest()

            if current_hash != previous_hash:
                unique_frames += 1
                previous_hash = current_hash

            time.sleep(0.005)

        elapsed = time.time() - start

        print("\n--- Camera FPS Results ---")
        print(f"Duration: {elapsed:.2f} seconds")
        print(f"Total reads: {total_reads}")
        print(f"Changed image samples: {unique_frames}")
        print(f"Read rate: {total_reads / elapsed:.2f} reads/sec")
        print(f"Image-change rate: {unique_frames / elapsed:.2f} changes/sec")
        print(f"Connected at end: {camera.is_connected}")

finally:
    camera.release()