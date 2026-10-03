from importlib.metadata import PackageNotFoundError, distribution, distributions, version

import cv2
from packaging.markers import default_environment
from packaging.requirements import Requirement


EXPECTED = {
    "opencv-python-headless": "5.0.0.93",
    "scenedetect": "0.7.1",
}


def main() -> int:
    installed_names = {
        str(item.metadata.get("Name", "")).lower()
        for item in distributions()
    }
    if "opencv-python" in installed_names:
        raise RuntimeError("GUI opencv-python must not be installed.")
    for package_name, expected_version in EXPECTED.items():
        actual_version = version(package_name)
        if actual_version != expected_version:
            raise RuntimeError(
                f"{package_name} version mismatch: {actual_version} != {expected_version}"
            )
    if cv2.__version__ != "5.0.0":
        raise RuntimeError(f"Unexpected cv2 version: {cv2.__version__}")

    marker_environment = default_environment()
    failures: list[str] = []
    for package in distributions():
        package_name = str(package.metadata.get("Name", package.metadata.get("Summary", "unknown")))
        for requirement_text in package.requires or []:
            requirement = Requirement(requirement_text)
            if requirement.marker and not requirement.marker.evaluate(marker_environment):
                continue
            try:
                dependency = distribution(requirement.name)
            except PackageNotFoundError:
                failures.append(f"{package_name}: missing {requirement}")
                continue
            if requirement.specifier and dependency.version not in requirement.specifier:
                failures.append(
                    f"{package_name}: {requirement.name} {dependency.version} does not satisfy "
                    f"{requirement.specifier}"
                )
    if failures:
        raise RuntimeError("Dependency consistency check failed:\n" + "\n".join(failures))
    print(
        "python_environment=valid "
        f"opencv={version('opencv-python-headless')} scenedetect={version('scenedetect')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())