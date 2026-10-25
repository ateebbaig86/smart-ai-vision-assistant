import time
from esp32_camera import ESP32Camera

camera = ESP32Camera()

try:
    connected = camera.connect()

    if not connected:
        print("ERROR: Could not connect to ESP32-CAM.")
    else:
        print("Starting 20-second stream test...")

        frame_count = 0
        start_time = time.time()
        last_frame_time = start_time

        while time.time() - start_time < 20:
            frame = camera.read()

            if frame is not None:
                frame_count += 1
                last_frame_time = time.time()

            time.sleep(0.01)

        elapsed = time.time() - start_time

        print("\n--- ESP32-CAM Stability Results ---")
        print(f"Frames received: {frame_count}")
        print(f"Test duration: {elapsed:.1f} seconds")
        print(f"Average FPS: {frame_count / elapsed:.2f}")
        print(f"Connected at end: {camera.is_connected}")

        if frame_count > 0:
            print(f"Last frame received: {last_frame_time - start_time:.2f}s")
        else:
            print("ERROR: No frames received.")

finally:
    camera.release()
    print("Test finished.")