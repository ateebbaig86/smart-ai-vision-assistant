import json
import os
from pathlib import Path

import cv2
import insightface
import numpy as np


class FaceEnrollmentManager:

    NUM_SAMPLES = 5

    def __init__(self, database_file=None):
        project_root = Path(__file__).resolve().parent

        if database_file is None:
            database_file = (
                project_root.parent
                / "face_database.json"
            )

        self.database_file = Path(database_file)

        self.app = None
        self.database = {}

        self.active_name = None
        self.embeddings = []
        self.active = False

        print("[FACE ENROLL] Initializing...")

        self._load_model()
        self._load_database()

    # =========================================================
    # LOAD INSIGHTFACE
    # =========================================================

    def _load_model(self):

        print("[FACE ENROLL] Loading InsightFace...")

        self.app = insightface.app.FaceAnalysis(
            name="buffalo_l",
            providers=["CPUExecutionProvider"]
        )

        self.app.prepare(
            ctx_id=0,
            det_size=(640, 640)
        )

        print("[FACE ENROLL] InsightFace loaded.")

    # =========================================================
    # LOAD DATABASE
    # =========================================================

    def _load_database(self):

        if not self.database_file.exists():

            print(
                f"[FACE ENROLL] Database does not exist yet: "
                f"{self.database_file}"
            )

            self.database = {}
            return

        try:

            with open(
                self.database_file,
                "r",
                encoding="utf-8"
            ) as file:

                database = json.load(file)

            if not isinstance(database, dict):
                raise ValueError(
                    "Face database must contain a JSON object."
                )

            self.database = database

            print(
                f"[FACE ENROLL] Database loaded: "
                f"{len(self.database)} people."
            )

            for name, samples in self.database.items():

                print(
                    f"[FACE ENROLL]   {name}: "
                    f"{len(samples)} samples"
                )

        except Exception as error:

            raise RuntimeError(
                f"Could not load face database: {error}"
            ) from error

    # =========================================================
    # START ENROLLMENT
    # =========================================================

    def start_enrollment(
        self,
        name,
        overwrite=False
    ):

        name = str(name).strip()

        if not name:
            return {
                "success": False,
                "message": "Name cannot be empty."
            }

        if len(name) > 100:
            return {
                "success": False,
                "message": "Name is too long."
            }

        if name in self.database and not overwrite:
            return {
                "success": False,
                "message": (
                    f"'{name}' already exists. "
                    "Explicit overwrite is required."
                ),
                "already_exists": True
            }

        self.active_name = name
        self.embeddings = []
        self.active = True

        print(
            f"[FACE ENROLL] Started enrollment for: {name}"
        )

        return {
            "success": True,
            "message": f"Enrollment started for {name}.",
            "name": name,
            "samples": 0,
            "required_samples": self.NUM_SAMPLES
        }

    # =========================================================
    # PROCESS ONE FRAME
    # =========================================================

    def process_frame(
        self,
        frame,
        capture=False
    ):

        if not self.active:

            return {
                "success": False,
                "message": "No enrollment session is active.",
                "face_count": 0,
                "samples": len(self.embeddings),
                "required_samples": self.NUM_SAMPLES
            }

        if frame is None:

            return {
                "success": False,
                "message": "Invalid camera frame.",
                "face_count": 0,
                "samples": len(self.embeddings),
                "required_samples": self.NUM_SAMPLES
            }

        try:

            faces = self.app.get(frame)

        except Exception as error:

            return {
                "success": False,
                "message": f"Face detection error: {error}",
                "face_count": 0,
                "samples": len(self.embeddings),
                "required_samples": self.NUM_SAMPLES
            }

        face_count = len(faces)

        # -----------------------------------------------------
        # No face
        # -----------------------------------------------------

        if face_count == 0:

            return {
                "success": False,
                "message": "No face detected.",
                "face_count": 0,
                "samples": len(self.embeddings),
                "required_samples": self.NUM_SAMPLES
            }

        # -----------------------------------------------------
        # Multiple faces
        # -----------------------------------------------------

        if face_count > 1:

            return {
                "success": False,
                "message": (
                    "Multiple faces detected. "
                    "Only one person should be visible."
                ),
                "face_count": face_count,
                "samples": len(self.embeddings),
                "required_samples": self.NUM_SAMPLES
            }

        face = faces[0]

        # -----------------------------------------------------
        # Preview only
        # -----------------------------------------------------

        if not capture:

            return {
                "success": True,
                "message": "One face detected.",
                "face_count": 1,
                "captured": False,
                "samples": len(self.embeddings),
                "required_samples": self.NUM_SAMPLES
            }

        # -----------------------------------------------------
        # Capture embedding
        # -----------------------------------------------------

        try:

            embedding = face.embedding.astype(
                np.float32
            )

            norm = np.linalg.norm(
                embedding
            )

            if norm == 0:

                return {
                    "success": False,
                    "message": "Invalid face embedding.",
                    "face_count": 1,
                    "samples": len(self.embeddings),
                    "required_samples": self.NUM_SAMPLES
                }

            embedding = embedding / norm

            if embedding.shape != (512,):

                return {
                    "success": False,
                    "message": (
                        f"Unexpected embedding size: "
                        f"{embedding.shape}"
                    ),
                    "face_count": 1,
                    "samples": len(self.embeddings),
                    "required_samples": self.NUM_SAMPLES
                }

            self.embeddings.append(
                embedding.tolist()
            )

        except Exception as error:

            return {
                "success": False,
                "message": (
                    f"Could not create embedding: {error}"
                ),
                "face_count": 1,
                "samples": len(self.embeddings),
                "required_samples": self.NUM_SAMPLES
            }

        sample_number = len(self.embeddings)

        print(
            f"[FACE ENROLL] Sample "
            f"{sample_number}/{self.NUM_SAMPLES} captured."
        )

        completed = (
            sample_number >= self.NUM_SAMPLES
        )

        if completed:

            save_result = self._save_enrollment()

            if not save_result["success"]:
                return save_result

            self.active = False

            completed_name = self.active_name

            self.active_name = None
            self.embeddings = []

            return {
                "success": True,
                "message": (
                    f"Enrollment completed for "
                    f"{completed_name}."
                ),
                "completed": True,
                "name": completed_name,
                "captured": True,
                "samples": self.NUM_SAMPLES,
                "required_samples": self.NUM_SAMPLES
            }

        return {
            "success": True,
            "message": (
                f"Sample {sample_number}/"
                f"{self.NUM_SAMPLES} captured."
            ),
            "completed": False,
            "captured": True,
            "samples": sample_number,
            "required_samples": self.NUM_SAMPLES
        }

    # =========================================================
    # SAVE DATABASE
    # =========================================================

    def _save_enrollment(self):

        if not self.active_name:
            return {
                "success": False,
                "message": "No enrollment name available."
            }

        if len(self.embeddings) != self.NUM_SAMPLES:
            return {
                "success": False,
                "message": (
                    "Enrollment is incomplete. "
                    f"Have {len(self.embeddings)}/"
                    f"{self.NUM_SAMPLES} samples."
                )
            }

        new_database = dict(self.database)

        new_database[self.active_name] = list(
            self.embeddings
        )

        self.database_file.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        temporary_file = self.database_file.with_suffix(
            ".json.tmp"
        )

        try:

            with open(
                temporary_file,
                "w",
                encoding="utf-8"
            ) as file:

                json.dump(
                    new_database,
                    file,
                    indent=4
                )

                file.flush()
                os.fsync(file.fileno())

            os.replace(
                temporary_file,
                self.database_file
            )

            self.database = new_database

            print(
                f"[FACE ENROLL] Database saved: "
                f"{self.database_file}"
            )

            return {
                "success": True,
                "message": "Database saved successfully."
            }

        except Exception as error:

            try:

                if temporary_file.exists():
                    temporary_file.unlink()

            except Exception:
                pass

            return {
                "success": False,
                "message": (
                    f"Could not save database: {error}"
                )
            }

    # =========================================================
    # CANCEL
    # =========================================================

    def cancel_enrollment(self):

        name = self.active_name

        self.active = False
        self.active_name = None
        self.embeddings = []

        print(
            "[FACE ENROLL] Enrollment cancelled."
        )

        return {
            "success": True,
            "message": "Enrollment cancelled.",
            "name": name
        }

    # =========================================================
    # STATUS
    # =========================================================

    def get_status(self):

        return {
            "active": self.active,
            "name": self.active_name,
            "samples": len(self.embeddings),
            "required_samples": self.NUM_SAMPLES,
            "database_file": str(
                self.database_file
            ),
            "people_count": len(self.database)
        }

    # =========================================================
    # RELOAD DATABASE
    # =========================================================

    def reload_database(self):

        self._load_database()

        return {
            "success": True,
            "people_count": len(self.database)
        }
