"""Every public version surface describes the same release."""
import re
from pathlib import Path


def test_package_and_project_versions_agree():
    import lyrica

    project = Path(__file__).parents[1] / "pyproject.toml"
    declared = re.search(r'^version = "([^"]+)"$',
                         project.read_text(encoding="utf-8"), re.MULTILINE)
    assert declared is not None
    assert lyrica.__version__ == declared.group(1)


def test_runtime_user_agents_name_the_current_version():
    import lyrica
    from lyrica import artwork, sponsorblock, youtube
    from lyrica.providers import community, lrclib

    for module in (artwork, sponsorblock, youtube, community, lrclib):
        assert f"lyrica/{lyrica.__version__}" in module.HEADERS["User-Agent"]
