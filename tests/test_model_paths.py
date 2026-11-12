"""Model paths of older tsOS-audio images, or given only by name, resolve to the installed models."""

import logging

import pytest
import yaml

from app.configs import model_paths
from app.configs.model_paths import match_model, resolve_config_models, resolve_model_path
from app.configs.soundscapepipe import SoundscapepipeConfig

# the models as installed on tsOS-audio 2027
FILES = [
    "pybirdedge/models/EFF_B0.onnx",
    "pybirdedge/models/ger/MarBird_EFL0_GER.onnx",
    "yolobat/models/yolobat11_2026.1.1/model.xml",
    "yolobat/models/yolobat11_2026.1.1/model.bin",
    "yolobat/models/yolobat11_2026.1.1/metadata.yaml",
    "yolobat/models/yolobat11_2026.3.1/model.xml",
    "yolobat/models/yolobat11_2026.3.1/model.bin",
    "yolobat/models/yolobat11_2026.3.1/metadata.yaml",
]


@pytest.fixture
def opt(tmp_path):
    for f in FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_bytes(b"")
    return tmp_path


@pytest.fixture
def roots(opt):
    return {"birdedge": [str(opt / "pybirdedge/models")], "yolobat": [str(opt / "yolobat/models")]}


@pytest.mark.parametrize(
    ("detector", "ref", "expected"),
    [
        # paths of older images
        (
            "birdedge",
            "/home/pi/pybirdedge/birdedge/models/ger/MarBird_EFL0_GER.onnx",
            "pybirdedge/models/ger/MarBird_EFL0_GER.onnx",
        ),
        (
            "birdedge",
            "/home/pi/birdedge/models/ger/MarBird_EFL0_GER.onnx",
            "pybirdedge/models/ger/MarBird_EFL0_GER.onnx",
        ),
        ("birdedge", "/home/pi/pybirdedge/birdedge/models/EFF_B0.onnx", "pybirdedge/models/EFF_B0.onnx"),
        (
            "yolobat",
            "/home/pi/yolobat/models/yolobat11_2026.3.1/model.xml",
            "yolobat/models/yolobat11_2026.3.1/model.xml",
        ),
        # only the model's name, with or without extension
        ("birdedge", "MarBird_EFL0_GER.onnx", "pybirdedge/models/ger/MarBird_EFL0_GER.onnx"),
        ("birdedge", "MarBird_EFL0_GER", "pybirdedge/models/ger/MarBird_EFL0_GER.onnx"),
        # only the folder holding the model
        ("yolobat", "yolobat11_2026.1.1", "yolobat/models/yolobat11_2026.1.1/model.xml"),
        ("yolobat", "/home/pi/yolobat/models/yolobat11_2026.3.1", "yolobat/models/yolobat11_2026.3.1/model.xml"),
    ],
)
def test_resolves(opt, roots, detector, ref, expected):
    assert resolve_model_path(ref, detector, roots) == str(opt / expected)


@pytest.mark.parametrize(
    ("detector", "ref"),
    [
        ("yolobat", "model.xml"),  # in both folders
        ("yolobat", "/home/pi/yolobat/models/yolobat11_2025.6.2/model.xml"),  # not installed
        ("yolobat", "yolobat11_2025.6.2"),  # not installed
        ("birdedge", "MarBird_EFL0_NOR.onnx"),  # not installed
    ],
)
def test_unresolved_stays(roots, detector, ref, caplog):
    with caplog.at_level(logging.WARNING):
        assert resolve_model_path(ref, detector, roots) == ref
    assert "matches no single installed model" in caplog.text


def test_without_installed_models_nothing_happens(tmp_path, caplog):
    # ecohub (server mode) has no /opt models: paths are kept, nothing is logged
    roots = {"birdedge": [str(tmp_path / "missing")]}
    with caplog.at_level(logging.WARNING):
        assert resolve_model_path("/home/pi/x/MarBird_EFL0_GER.onnx", "birdedge", roots) == (
            "/home/pi/x/MarBird_EFL0_GER.onnx"
        )
    assert not caplog.text


def test_other_version_never_matches():
    # a station with a single YOLOBat model must not run it for a config naming another
    installed = ["yolobat11_2026.3.1/model.xml"]
    assert match_model("/home/pi/yolobat/models/yolobat11_2025.6.2/model.xml", installed) is None
    assert match_model("yolobat11_2025.6.2", installed) is None
    assert match_model("/home/pi/yolobat/models/yolobat11_2026.3.1/model.xml", installed) == installed[0]


def test_resolve_config_models(opt, roots):
    config = {
        "detectors": {
            "birdedge": {
                "model_path": "/home/pi/pybirdedge/birdedge/models/ger/MarBird_EFL0_GER.onnx",
                "call_model_path": "EFF_B0",
                "class_threshold": 0.3,
            },
            "yolobat": {"model_path": "yolobat11_2026.3.1"},
            "schedule": {"tasks": []},
        }
    }
    changed = resolve_config_models(config, roots)
    assert config["detectors"]["birdedge"] == {
        "model_path": str(opt / "pybirdedge/models/ger/MarBird_EFL0_GER.onnx"),
        "call_model_path": str(opt / "pybirdedge/models/EFF_B0.onnx"),
        "class_threshold": 0.3,
    }
    assert config["detectors"]["yolobat"] == {"model_path": str(opt / "yolobat/models/yolobat11_2026.3.1/model.xml")}
    assert changed == {
        "/home/pi/pybirdedge/birdedge/models/ger/MarBird_EFL0_GER.onnx": str(
            opt / "pybirdedge/models/ger/MarBird_EFL0_GER.onnx"
        ),
        "EFF_B0": str(opt / "pybirdedge/models/EFF_B0.onnx"),
        "yolobat11_2026.3.1": str(opt / "yolobat/models/yolobat11_2026.3.1/model.xml"),
    }


def test_save_writes_resolved_paths(opt, roots, tmp_path, monkeypatch):
    # every upload (single file, zip) and UI save ends in SoundscapepipeConfig.save
    monkeypatch.setattr(model_paths, "MODEL_ROOTS", roots)
    cfg = SoundscapepipeConfig(tmp_path)
    result = cfg.save(
        {"detectors": {"yolobat": {"model_path": "/home/pi/yolobat/models/yolobat11_2026.1.1/model.xml"}}}
    )
    resolved = str(opt / "yolobat/models/yolobat11_2026.1.1/model.xml")
    assert yaml.safe_load(cfg.config_file.read_text())["detectors"]["yolobat"]["model_path"] == resolved
    assert result == {
        "model_paths_resolved": {"/home/pi/yolobat/models/yolobat11_2026.1.1/model.xml": resolved},
    }


def test_save_without_changes_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(model_paths, "MODEL_ROOTS", {})
    cfg = SoundscapepipeConfig(tmp_path)
    assert cfg.save({"detectors": {"birdedge": {"model_path": "/home/pi/x.onnx"}}}) is None
