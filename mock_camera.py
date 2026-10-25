import cv2
import glob
import time


class MockCamera:
    """
    Local test camera.

    Uses existing project images instead of an ESP32-CAM.
    This allows the complete detection pipeline to be tested
    before the physical hardware is available.
    """

    def __init__(self, image_folder):
        self.image_folder = image_folder
        self.image_paths = sorted(
            glob.glob(f"{image_folder}/*.jpg")
            + glob.glob(f"{image_folder}/*.jpeg")
            + glob.glob(f"{image_folder}/*.png")
        )

        self.index = 0

        if not self.image_paths:
            raise FileNotFoundError(
                f"No test images found in:\n{image_folder}"
            )

        print(f"[MOCK CAMERA] Found {len(self.image_paths)} images.")

    def read(self):
        """
        Return the next test image.

        Returns:
            OpenCV BGR frame, or None when all images are finished.
        """

        if self.index >= len(self.image_paths):
            return None

        image_path = self.image_paths[self.index]

        print(
            f"[MOCK CAMERA] "
            f"{self.index + 1}/{len(self.image_paths)}: "
            f"{image_path}"
        )

        frame = cv2.imread(image_path)

        self.index += 1

        if frame is None:
            print(f"[MOCK CAMERA] Failed to load: {image_path}")
            return None

        return frame

    def reset(self):
        """Start the image sequence again."""
        self.index = 0

    def release(self):
        """Compatibility method with the real camera."""
        print("[MOCK CAMERA] Released.")