from backend.build_sidecar import SPEC, smoke_check


def test_desktop_sidecar_spec_uses_slim_runtime_hooks():
    result = smoke_check()
    source = SPEC.read_text(encoding="utf-8")

    assert result["ready"] is True
    assert 'hookspath=["pyinstaller-hooks"]' in source
    for excluded in ("pandas", "scipy", "matplotlib", "PIL", "pytest"):
        assert f'"{excluded}"' in source
