import cv2
import time

STREAM_URL = "http://10.61.75.117/stream"
TEST_DURATION = 15

print("=" * 55)
print("ESP32-CAM STREAM TEST")
print("=" * 55)
print(f"Stream URL: {STREAM_URL}")
print("Connecting...")

cap = None
frames_received = 0
failed_reads = 0
start_time = time.time()
last_frame_time = None
frame_shape = None

try:
    params = [
        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
        cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000,
    ]

    cap = cv2.VideoCapture(
        STREAM_URL,
        cv2.CAP_FFMPEG,
        params
    )

    if not cap.isOpened():
        print("ERROR: Could not open the ESP32 stream.")
        print("Check the ESP32 power, Wi-Fi, IP address, and stream URL.")
    else:
        print("Connected. Receiving frames for 15 seconds...")
        start_time = time.time()

        while time.time() - start_time < TEST_DURATION:
            success, frame = cap.read()

            if not success or frame is None:
                failed_reads += 1
                print("WARNING: Frame read failed.")
                break

            frames_received += 1
            last_frame_time = time.time()
            frame_shape = frame.shape

        elapsed = time.time() - start_time

        print("\n" + "=" * 55)
        print("TEST RESULTS")
        print("=" * 55)
        print(f"Test duration:       {elapsed:.2f} seconds")
        print(f"Frames received:     {frames_received}")
        print(f"Failed reads:        {failed_reads}")
        print(f"Average FPS:         {frames_received / elapsed:.2f}")

        if frame_shape:
            height, width = frame_shape[:2]
            print(f"Frame resolution:    {width} x {height}")

        if frames_received > 0:
            print("RESULT: Frames received successfully.")
        else:
            print("RESULT: No frames received.")

except Exception as exc:
    print(f"TEST ERROR: {type(exc).__name__}: {exc}")

finally:
    if cap is not None:
        cap.release()
    print("\nCamera connection released.")
    print("Test finished.")
