"""Resolve soundscapepipe model paths to the models installed on this station.

Configs written for older tsOS-audio images (and by ecohub, which still serves
stations running them) name models under `/home/pi/...`
(`/home/pi/pybirdedge/birdedge/models/ger/MarBird_EFL0_GER.onnx`); tsOS-audio
2027 installs them root-owned under `/opt/<package>/models`. A config may also
name just the model (`MarBird_EFL0_GER.onnx`, `MarBird_EFL0_GER`) or the folder
holding it (`yolobat11_2026.3.1`).

A path that exists is kept. Otherwise every model file below the detector's
model folder is a candidate, compared by trailing path components: as a file
first, then as the folder holding the file. The configured path's last component
also matches a file name without its extension. A match must cover the
candidate's whole path below its models folder, unless the configured path ends
first: so `.../yolobat11_2025.6.2/model.xml` never matches
`yolobat11_2026.3.1/model.xml`. The longest match wins; a tie, or no match,
leaves the path unchanged.

soundscapepipe resolves the same way when it loads its config
(soundscapepipe/models.py), for configs that never pass through tsconfig.
"""

import logging
import os
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any

logger = logging.getLogger(__name__)

MODEL_ROOTS: dict[str, list[str]] = {
    "birdedge": ["/opt/pybirdedge/models"],
    "yolobat": ["/opt/yolobat/models"],
    "audioprotopnet": ["/opt/audioprotopnet/models"],
}
MODEL_EXTENSIONS = (".onnx", ".xml")


def _parts(path: str) -> tuple[str, ...]:
    return tuple(p for p in PurePosixPath(path).parts if p != "/")


def model_files(roots: Iterable[str]) -> list[tuple[str, str]]:
    """(root, path relative to it) of all model files (`.onnx`, `.xml`) below `roots`."""
    found = []
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            found.extend(
                (root, os.path.relpath(os.path.join(dirpath, f), root)) for f in files if f.endswith(MODEL_EXTENSIONS)
            )
    return sorted(found)


def _tail_match(ref: tuple[str, ...], rel: tuple[str, ...]) -> int:
    """Trailing components `ref` and `rel` share, or 0 unless that covers `rel` or all of `ref`."""
    n = 0
    while n < min(len(ref), len(rel)):
        r, c = ref[-1 - n], rel[-1 - n]
        stem, ext = os.path.splitext(c)
        if r != c and not (n == 0 and ext in MODEL_EXTENSIONS and r == stem):
            break
        n += 1
    return n if n in (len(ref), len(rel)) else 0


def match_model(ref: str, candidates: Iterable[str]) -> str | None:
    """The candidate (a path relative to its models folder) `ref` names unambiguously, or None."""
    parts = _parts(ref)
    if not parts:
        return None
    scored = []
    for cand in candidates:
        rel = _parts(cand)
        # as a file first, then as the folder holding the file
        scored.append(((_tail_match(parts, rel), _tail_match(parts, rel[:-1]) if len(rel) > 1 else 0), cand))
    best = max((score for score, _ in scored), default=(0, 0))
    if best == (0, 0):
        return None
    winners = [cand for score, cand in scored if score == best]
    return winners[0] if len(winners) == 1 else None


def resolve_model_path(ref: str, detector: str, roots: dict[str, list[str]] | None = None) -> str:
    """`ref` if it exists, else the installed model it names, else `ref` unchanged.

    Without an installed models folder for `detector` (ecohub, server mode) `ref` is kept silently.
    """
    detector_roots = [r for r in (roots if roots is not None else MODEL_ROOTS).get(detector, []) if os.path.isdir(r)]
    if not isinstance(ref, str) or not ref or not detector_roots or os.path.isfile(ref):
        return ref
    by_rel: dict[str, list[str]] = {}
    for root, rel in model_files(detector_roots):
        by_rel.setdefault(rel, []).append(os.path.join(root, rel))
    match = match_model(ref, by_rel)
    if match is None or len(by_rel[match]) > 1:
        logger.warning("%s: model '%s' not found and matches no single installed model", detector, ref)
        return ref
    return by_rel[match][0]


def resolve_config_models(config: dict[str, Any], roots: dict[str, list[str]] | None = None) -> dict[str, str]:
    """Rewrite the detectors' `*model_path` settings of a soundscapepipe config in place.

    Returns the rewritten paths as {old: new}.
    """
    changed: dict[str, str] = {}
    detectors = config.get("detectors")
    if not isinstance(detectors, dict):
        return changed
    for detector, settings in detectors.items():
        if not isinstance(settings, dict):
            continue
        for key, ref in settings.items():
            if not key.endswith("model_path"):
                continue
            resolved = resolve_model_path(ref, detector, roots)
            if resolved != ref:
                logger.info("%s: %s '%s' resolved to %s", detector, key, ref, resolved)
                settings[key] = resolved
                changed[ref] = resolved
    return changed
