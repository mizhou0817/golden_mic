# Vendored Python artifact

`scenedetect-0.7.1-py3-none-any.whl` is rebuilt deterministically from the official PyPI SceneDetect 0.7.1 wheel. Application code is unchanged. The only metadata change replaces its GUI OpenCV dependency:

- upstream: `opencv-python`
- deployment: `opencv-python-headless==5.0.0.93`

This prevents `opencv-python` and `opencv-python-headless` from installing into the same `cv2` namespace on a headless Linux server.

Audited hashes:

- Upstream wheel SHA-256: `91b67902275b2e0d29a12f6d70435f04e0bd7852a6dc8a69c901c6069328dffa`
- Patched wheel SHA-256: `e7641810fa6b2007fc08dc09c5990feebd981f1bbf4b7bc616b30fd67566b0f2`

Regenerate with `build_scenedetect_headless_wheel.py`. The script refuses to patch an upstream artifact whose SHA-256 differs from the audited input and rebuilds the wheel `RECORD` file.
