import json
import threading

import pytest

from stardust_sdk import Stardust


class FakeCore:
    """Stands in for Stardust Core's /v1/events. ``script`` is a list of statuses or exceptions."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.events = []
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self, url, body, timeout):
        with self.lock:
            self.calls += 1
            step = self.script.pop(0) if self.script else 200
        if isinstance(step, Exception):
            raise step
        event = json.loads(body)
        if step == 200:
            self.events.append(event)
            return 200, json.dumps({**event, "indicator_code": "B2-S"}).encode()
        return step, b'{"detail": "nope"}'


@pytest.fixture
def core():
    return FakeCore()


@pytest.fixture
def stardust(core):
    sd = Stardust("http://core.test", region="us-east-1", transport=core)
    yield sd
    sd.close(1)
