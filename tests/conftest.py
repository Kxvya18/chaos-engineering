from pathlib import Path

import pytest

from chaosctl import config, faults, selftest
from chaosctl.sim import FakeDocker, FakeHttp

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg(tmp_path):
    return selftest.fast_config(config.load(ROOT / "config" / "sandbox.toml"), tmp_path)


@pytest.fixture
def env(cfg):
    return selftest.Env(cfg)


@pytest.fixture
def ctx(cfg):
    return faults.Ctx(cfg, FakeDocker(cfg), FakeHttp(), sleep=lambda s: None)
