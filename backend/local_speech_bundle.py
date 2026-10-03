"""Read-only configured-bundle binding, not authenticity or inference proof.

The production packaging verifier ships with deploy and uses only the stdlib.
Import it lazily: no Settings dependency, native runtime, model loader or scan
for possible manifests. A quiescent operator-controlled installation is required.
"""
from pathlib import Path


def local_speech_bundle_integrity_error(
    manifest: Path | None,
    speaker: Path | None,
    alignment: Path | None,
    *,
    license_reviewed: bool,
) -> str | None:
    """Return only authored static codes; None means prerequisite integrity only."""
    if manifest is None:
        return "bundle_manifest_missing"
    if not license_reviewed:
        return "model_license_and_language_not_reviewed"
    if speaker is None or alignment is None:
        return "bundle_path_binding_failed"
    try:
        # Compare supplied paths BEFORE opening any bytes. Do not resolve aliases
        # into acceptance (Path equality on Windows also folds case, so use str).
        root = speaker.parent.parent
        if (not root.is_absolute() or not manifest.is_absolute()
                or str(speaker) != str(root / "speaker" / "model.onnx")
                or str(alignment) != str(root / "ctc")):
            return "bundle_path_binding_failed"

        from deploy import verify_local_speech_bundle as verifier

        # Reuse the verifier's canonical/no-link policy for BOTH configured
        # paths, not just its root argument. These calls read metadata only.
        for path, directory in ((root, True), (speaker, False), (alignment, True), (manifest, False)):
            verifier._absolute(path, directory=directory)  # pyright: ignore[reportPrivateUsage] -- Shared packaging canonical policy.
        # Full bounded inventory/schema/hash verification on EVERY preflight.
        # Do not project the receipt, source assertions, paths or exception text.
        receipt = verifier.verify_local_speech_bundle(root, manifest)
        if receipt.get("ok") is not True:
            return "bundle_validation_failed"
    except Exception:
        return "bundle_validation_failed"
    return None