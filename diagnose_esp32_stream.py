import time
import urllib.request

from config import ESP32_STREAM_URL


def diagnose_stream(duration=15):
    print(f"Stream URL: {ESP32_STREAM_URL}")
    print(f"Testing for {duration} seconds...")

    request = urllib.request.Request(
        ESP32_STREAM_URL,
        headers={"User-Agent": "ESP32-CAM-Diagnostic/1.0"},
    )

    frame_count = 0
    byte_count = 0
    buffer = bytearray()

    start = time.monotonic()
    last_report = start

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            print("HTTP status:", response.status)
            print("Content-Type:", response.headers.get("Content-Type"))

            while time.monotonic() - start < duration:
                chunk = response.read(4096)

                if not chunk:
                    print("Stream ended.")
                    break

                byte_count += len(chunk)
                buffer.extend(chunk)

                # Count complete JPEG images using SOI and EOI markers.
                while True:
                    soi = buffer.find(b"\xff\xd8")

                    if soi < 0:
                        # Keep a possible partial JPEG marker.
                        if len(buffer) > 1:
                            del buffer[:-1]
                        break

                    if soi > 0:
                        del buffer[:soi]

                    eoi = buffer.find(b"\xff\xd9", 2)

                    if eoi < 0:
                        break

                    del buffer[:eoi + 2]
                    frame_count += 1

                now = time.monotonic()

                if now - last_report >= 5:
                    elapsed = now - start
                    print(
                        f"{elapsed:.1f}s | "
                        f"JPEG frames: {frame_count} | "
                        f"FPS: {frame_count / elapsed:.2f} | "
                        f"Received: {byte_count / 1024:.1f} KB"
                    )
                    last_report = now

    except Exception as exc:
        print("Stream diagnostic error:", repr(exc))

    finally:
        elapsed = time.monotonic() - start
        print("\n--- Final Results ---")
        print(f"Duration: {elapsed:.2f} seconds")
        print(f"Complete JPEG frames: {frame_count}")
        print(f"Average JPEG FPS: {frame_count / elapsed:.2f}")
        print(f"Data received: {byte_count / 1024:.1f} KB")
        print(f"Average data rate: {byte_count / elapsed / 1024:.2f} KB/s")


if __name__ == "__main__":
    diagnose_stream()