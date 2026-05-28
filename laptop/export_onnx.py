"""
export_onnx.py – Convert MediaPipe hand landmark TFLite model → ONNX
=====================================================================
Run this ONCE before using the ONNX+CUDA backend:

    cd laptop
    venv\\Scripts\\activate
    pip install onnxruntime-gpu tf2onnx tensorflow
    python export_onnx.py

Output: laptop/hand_landmark.onnx  (~9 MB)

The script downloads the MediaPipe hand_landmark_full.tflite model from the
official MediaPipe GitHub release and converts it to ONNX format using tf2onnx.

Requirements:
    pip install tf2onnx tensorflow onnxruntime-gpu
"""

import os
import sys
import urllib.request

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "hand_landmark.onnx")
TFLITE_PATH = os.path.join(os.path.dirname(__file__), "hand_landmark_full.tflite")

# Official MediaPipe hand landmark full model
TFLITE_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
)

# Simpler direct tflite URL (the .task file is a zip; use the raw tflite instead)
TFLITE_URL_DIRECT = (
    "https://storage.googleapis.com/mediapipe-assets/"
    "hand_landmark_full.tflite"
)


def download_tflite():
    if os.path.exists(TFLITE_PATH):
        print(f"[EXPORT] TFLite model already exists: {TFLITE_PATH}")
        return
    print(f"[EXPORT] Downloading hand_landmark_full.tflite ...")
    try:
        urllib.request.urlretrieve(TFLITE_URL_DIRECT, TFLITE_PATH)
        print(f"[EXPORT] Downloaded to {TFLITE_PATH}")
    except Exception as e:
        print(f"[EXPORT] Download failed: {e}")
        print("[EXPORT] Please manually download hand_landmark_full.tflite from:")
        print("         https://storage.googleapis.com/mediapipe-assets/hand_landmark_full.tflite")
        print(f"         and place it at: {TFLITE_PATH}")
        sys.exit(1)


def convert_to_onnx():
    if os.path.exists(OUTPUT_PATH):
        print(f"[EXPORT] ONNX model already exists: {OUTPUT_PATH}")
        print("[EXPORT] Delete it and re-run to reconvert.")
        return

    print("[EXPORT] Converting TFLite → ONNX (this takes ~30 seconds)...")
    try:
        import subprocess
        result = subprocess.run(
            [
                sys.executable, "-m", "tf2onnx.convert",
                "--tflite", TFLITE_PATH,
                "--output", OUTPUT_PATH,
                "--opset", "13",
            ],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print("[EXPORT] tf2onnx conversion failed:")
            print(result.stderr)
            sys.exit(1)
        print(f"[EXPORT] ONNX model saved to: {OUTPUT_PATH}")
    except Exception as e:
        print(f"[EXPORT] Conversion error: {e}")
        sys.exit(1)


def verify_onnx():
    print("[EXPORT] Verifying ONNX model with onnxruntime...")
    try:
        import onnxruntime as ort
        import numpy as np

        providers = ort.get_available_providers()
        print(f"[EXPORT] Available providers: {providers}")

        ep = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        sess = ort.InferenceSession(OUTPUT_PATH, providers=ep)
        print(f"[EXPORT] Session providers: {sess.get_providers()}")

        inp_name  = sess.get_inputs()[0].name
        inp_shape = sess.get_inputs()[0].shape
        print(f"[EXPORT] Input: {inp_name}  shape: {inp_shape}")

        # Run a dummy inference
        dummy = np.zeros((1, 224, 224, 3), dtype=np.float32)
        outputs = sess.run(None, {inp_name: dummy})
        print(f"[EXPORT] Output shapes: {[o.shape for o in outputs]}")
        print("[EXPORT] Verification PASSED")

        if "CUDAExecutionProvider" in sess.get_providers():
            print("[EXPORT] CUDA is active – GPU inference will be used")
        else:
            print("[EXPORT] WARNING: CUDA not active – will run on CPU")
            print("         Make sure onnxruntime-gpu is installed and CUDA drivers are up to date")

    except ImportError:
        print("[EXPORT] onnxruntime not installed – run: pip install onnxruntime-gpu")
    except Exception as e:
        print(f"[EXPORT] Verification failed: {e}")


if __name__ == "__main__":
    print("=" * 54)
    print("  MediaPipe Hand Landmark → ONNX Exporter")
    print("=" * 54)
    download_tflite()
    convert_to_onnx()
    verify_onnx()
    print()
    print("Done. Run hand_detect.py – it will automatically use the ONNX+CUDA backend.")
