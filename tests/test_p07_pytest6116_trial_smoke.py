"""Trial smoke stays isolated and verifies all 32 publisher-wheel plans."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import p07_pytest6116_trial_smoke as smoke  # noqa: E402


def test_native_probe_uses_fixed_trial_and_zope_paths():
    compile(smoke.PROBE, '<trial-native-smoke>', 'exec')
    assert "metadata.version('setuptools')=='57.5.0'" in smoke.PROBE
    assert "site+'twisted/trial/unittest.py'" in smoke.PROBE
    assert '_zope_interface_coptimizations' in smoke.PROBE


def test_trial_prepare_profile_does_not_conflate_ordinary_admission():
    profile = json.loads(smoke.trial.PROFILE.read_bytes())
    assert len(profile['wheels']) == 7
    assert profile['base_optional_profile_sha256'] == smoke.optional.PEXPECT_PROFILE_SHA256
    with pytest.raises(ValueError, match='fresh output'):
        smoke.run(Path('.'))
