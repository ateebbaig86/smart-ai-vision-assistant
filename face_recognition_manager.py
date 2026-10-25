import json
from pathlib import Path

import insightface
import numpy as np


class FaceRecognitionManager:

    DEFAULT_THRESHOLD = 0.45

    def __init__(self, database_file=None, threshold=None):
        project_root = Path(__file__).resolve().parent

        if database_file is None:
            database_file = (
                project_root.parent
                / "face_database.json"
            )

        self.database_file = Path(database_file)

        self.threshold = (
            self.DEFAULT_THRESHOLD
            if threshold is None
            else float(threshold)
        )

        self.app = None
        self.database = {}

        print("[FACE RECOGNITION] Initializing...")

        self._load_model()
        self._load_database()

    def _load_model(self):
        print("[FACE RECOGNITION] Loading InsightFace...")

        self.app = insightface.app.FaceAnalysis(
            name="buffalo_l",
            providers=["CPUExecutionProvider"]
        )

        self.app.prepare(
            ctx_id=0,
            det_size=(640, 640)
        )

        print("[FACE RECOGNITION] InsightFace loaded.")

    def _load_database(self):
        if not self.database_file.exists():
            print(
                f"[FACE RECOGNITION] Database not found: "
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
                f"[FACE RECOGNITION] Database loaded: "
                f"{len(self.database)} people."
            )

            for name, samples in self.database.items():
                print(
                    f"[FACE RECOGNITION]   {name}: "
                    f"{len(samples)} samples"
                )

        except Exception as error:
            raise RuntimeError(
                f"Could not load face database: {error}"
            ) from error

    def reload_database(self):
        self._load_database()

        return {
            "success": True,
            "people_count": len(self.database)
        }

    @staticmethod
    def _normalize_embedding(embedding):
        embedding = np.asarray(
            embedding,
            dtype=np.float32
        )

        norm = np.linalg.norm(embedding)

        if norm == 0:
            return None

        return embedding / norm

    def _extract_embedding(self, face):
        try:
            embedding = face.embedding

            if embedding is None:
                return None

            embedding = self._normalize_embedding(
                embedding
            )

            if embedding is None:
                return None

            if embedding.shape != (512,):
                return None

            return embedding

        except Exception:
            return None

    def _find_best_match(self, query_embedding):
        best_name = None
        best_similarity = -1.0

        for name, samples in self.database.items():

            if not isinstance(samples, list):
                continue

            for sample in samples:
                try:
                    sample_embedding = (
                        self._normalize_embedding(sample)
                    )

                    if sample_embedding is None:
                        continue

                    if sample_embedding.shape != (512,):
                        continue

                    similarity = float(
                        np.dot(
                            query_embedding,
                            sample_embedding
                        )
                    )

                    if similarity > best_similarity:
                        best_similarity = similarity
                        best_name = name

                except Exception:
                    continue

        return best_name, best_similarity

    def recognize_frame(self, frame):
        if frame is None:
            return {
                "success": False,
                "recognized": False,
                "name": None,
                "similarity": None,
                "face_count": 0,
                "message": "Invalid camera frame."
            }

        try:
            faces = self.app.get(frame)

        except Exception as error:
            return {
                "success": False,
                "recognized": False,
                "name": None,
                "similarity": None,
                "face_count": 0,
                "message": f"Face detection error: {error}"
            }

        face_count = len(faces)

        if face_count == 0:
            return {
                "success": True,
                "recognized": False,
                "name": None,
                "similarity": None,
                "face_count": 0,
                "message": "No face detected."
            }

        results = []

        for face in faces:

            embedding = self._extract_embedding(face)

            if embedding is None:
                results.append({
                    "recognized": False,
                    "name": None,
                    "similarity": None,
                    "candidate": None,
                    "bbox": [float(v) for v in face.bbox.tolist()]
                })
                continue

            best_name, best_similarity = (
                self._find_best_match(embedding)
            )

            recognized = (
                best_name is not None
                and best_similarity >= self.threshold
            )

            results.append({
                "recognized": recognized,
                "name": best_name if recognized else None,
                "similarity": best_similarity,
                "candidate": best_name,
                "bbox": [float(v) for v in face.bbox.tolist()]
            })

        primary = results[0]

        return {
            "success": True,
            "recognized": primary["recognized"],
            "name": primary["name"],
            "similarity": primary["similarity"],
            "candidate": primary.get("candidate"),
            "face_count": face_count,
            "results": results,
            "threshold": self.threshold,
            "message": (
                f"Recognized {primary['name']}."
                if primary["recognized"]
                else "Unknown person."
            )
        }

    def get_status(self):
        return {
            "database_file": str(self.database_file),
            "people_count": len(self.database),
            "threshold": self.threshold
        }
