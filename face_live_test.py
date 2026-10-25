import cv2
import time

from face_recognition_manager import FaceRecognitionManager


CAMERA_INDEX = 0
WINDOW_NAME = "Face Recognition Performance Test"

TEST_DURATION_SECONDS = 20


def main():

    print("=" * 60)
    print("SMART AI VISION ASSISTANT")
    print("FACE RECOGNITION PERFORMANCE TEST")
    print("=" * 60)

    print("\n[FACE] Loading InsightFace...")

    recognizer = FaceRecognitionManager()

    print("[FACE] Ready.")
    print("[FACE] Status:")
    print(recognizer.get_status())

    print(f"\n[CAMERA] Opening camera {CAMERA_INDEX}...")

    camera = cv2.VideoCapture(CAMERA_INDEX)

    if not camera.isOpened():

        print("[CAMERA] ERROR: Could not open camera.")
        return

    print("[CAMERA] Camera opened.")

    print("\n" + "=" * 60)
    print(f"TEST DURATION: {TEST_DURATION_SECONDS} seconds")
    print("Look at the camera normally.")
    print("Press Q to stop early.")
    print("=" * 60)

    # -----------------------------------------------------
    # Performance counters
    # -----------------------------------------------------

    total_frames = 0
    total_recognition_calls = 0
    recognized_count = 0

    recognition_times = []

    test_start = time.perf_counter()

    try:

        while True:

            # -------------------------------------------------
            # Read camera frame
            # -------------------------------------------------

            success, frame = camera.read()

            if not success or frame is None:

                print("[CAMERA] Frame read failed.")
                break

            total_frames += 1

            # -------------------------------------------------
            # Measure InsightFace processing time
            # -------------------------------------------------

            recognition_start = time.perf_counter()

            result = recognizer.recognize_frame(frame)

            recognition_end = time.perf_counter()

            processing_time = (
                recognition_end
                - recognition_start
            )

            recognition_times.append(
                processing_time
            )

            total_recognition_calls += 1

            # -------------------------------------------------
            # Recognition result
            # -------------------------------------------------

            if result.get(
                "recognized",
                False
            ):

                recognized_count += 1

                name = result.get(
                    "name",
                    "Unknown"
                )

                similarity = result.get(
                    "similarity"
                )

                if similarity is not None:

                    label = (
                        f"{name} "
                        f"{similarity:.3f}"
                    )

                else:

                    label = name

            elif result.get(
                "face_count",
                0
            ) > 0:

                label = "UNKNOWN"

            else:

                label = "NO FACE"

            # -------------------------------------------------
            # Display
            # -------------------------------------------------

            cv2.putText(
                frame,
                label,
                (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (0, 255, 0),
                2
            )

            cv2.putText(
                frame,
                f"Recognition: {processing_time * 1000:.0f} ms",
                (20, 75),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2
            )

            cv2.imshow(
                WINDOW_NAME,
                frame
            )

            # -------------------------------------------------
            # Test duration
            # -------------------------------------------------

            elapsed = (
                time.perf_counter()
                - test_start
            )

            if elapsed >= TEST_DURATION_SECONDS:

                break

            # -------------------------------------------------
            # Quit
            # -------------------------------------------------

            if (
                cv2.waitKey(1) & 0xFF
                == ord("q")
            ):

                print(
                    "\n[TEST] Q pressed."
                )

                break

    except KeyboardInterrupt:

        print(
            "\n[TEST] Keyboard interrupt."
        )

    finally:

        camera.release()
        cv2.destroyAllWindows()

    # =========================================================
    # Results
    # =========================================================

    total_time = (
        time.perf_counter()
        - test_start
    )

    print("\n")
    print("=" * 60)
    print("FACE RECOGNITION PERFORMANCE RESULTS")
    print("=" * 60)

    print(
        f"Test duration: "
        f"{total_time:.2f} seconds"
    )

    print(
        f"Camera frames: "
        f"{total_frames}"
    )

    print(
        f"Recognition calls: "
        f"{total_recognition_calls}"
    )

    if total_time > 0:

        camera_fps = (
            total_frames
            / total_time
        )

        print(
            f"Effective processing FPS: "
            f"{camera_fps:.2f}"
        )

    if recognition_times:

        average_time = (
            sum(recognition_times)
            / len(recognition_times)
        )

        minimum_time = min(
            recognition_times
        )

        maximum_time = max(
            recognition_times
        )

        print(
            f"Average recognition time: "
            f"{average_time * 1000:.1f} ms"
        )

        print(
            f"Fastest recognition: "
            f"{minimum_time * 1000:.1f} ms"
        )

        print(
            f"Slowest recognition: "
            f"{maximum_time * 1000:.1f} ms"
        )

    print(
        f"Recognized results: "
        f"{recognized_count}"
    )

    print("=" * 60)
    print("[TEST] Finished.")
    print("=" * 60)


if __name__ == "__main__":

    main()