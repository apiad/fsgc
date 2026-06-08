import yaml

from fsgc.config import Recovery, Signature, SignatureManager


def test_signature_sentinels_field():
    sig = Signature(
        name="Test",
        pattern="**/test",
        recovery=Recovery.LOCAL,
        sentinels=["*.o", "package.json"],
    )
    assert sig.sentinels == ["*.o", "package.json"]

    sig_default = Signature(name="Default", pattern="**/def", recovery=Recovery.TRIVIAL)
    assert sig_default.sentinels == []


def test_signature_manager_loads_sentinels(tmp_path):
    config_file = tmp_path / "signatures.yaml"
    content = {
        "signatures": [
            {
                "name": "C++ Build",
                "pattern": "**/build",
                "recovery": "local",
                "sentinels": ["*.o", "*.a"],
            },
            {"name": "Generic", "pattern": "**/__pycache__", "recovery": "trivial"},
        ]
    }
    with open(config_file, "w") as f:
        yaml.dump(content, f)

    manager = SignatureManager(config_path=config_file)
    assert len(manager.signatures) == 2

    cpp_sig = next(s for s in manager.signatures if s.name == "C++ Build")
    assert cpp_sig.sentinels == ["*.o", "*.a"]
    assert cpp_sig.recovery == Recovery.LOCAL

    gen_sig = next(s for s in manager.signatures if s.name == "Generic")
    assert gen_sig.sentinels == []
    assert gen_sig.recovery == Recovery.TRIVIAL
