import time
import urllib.error

from conftest import FakeCore
from stardust_sdk import Stardust


def test_record_sends_event(stardust, core):
    stardust.record("anthropic", "claude-sonnet-5", 100, 20, tokens_cached_in=50)
    assert stardust.flush(2)
    [e] = core.events
    assert e["source_layer"] == "infra_agent"
    assert e["tokens_estimated"] is False
    assert (e["tokens_in"], e["tokens_out"], e["tokens_cached_in"], e["region"]) == (100, 20, 50, "us-east-1")
    assert e["timestamp"][-6] in "+-"  # local UTC offset kept
    assert "user_id" not in e  # optional fields omitted when unset


def test_on_result_receives_enriched_event():
    results = []
    sd = Stardust("http://core.test", transport=FakeCore(), on_result=results.append)
    sd.record("openai", "gpt-4o", 1, 1)
    assert sd.flush(2)
    assert results[0]["indicator_code"] == "B2-S"
    sd.close(1)


def test_retries_while_core_is_down():
    core = FakeCore([urllib.error.URLError("refused"), 503, 200])
    sd = Stardust("http://core.test", transport=core)
    sd.record("openai", "gpt-4o", 1, 1)
    assert sd.flush(10)  # flush() skips the backoff wait
    assert core.calls == 3
    assert len(core.events) == 1
    sd.close(1)


def test_rejected_events_are_dropped_not_retried():
    core = FakeCore([422])
    sd = Stardust("http://core.test", transport=core)
    sd.record("openai", "gpt-4o", 1, 1)
    assert sd.flush(2)
    assert core.calls == 1 and core.events == []
    sd.close(1)


def test_flush_times_out_while_down_and_keeps_events():
    core = FakeCore([urllib.error.URLError("down")] * 1000)
    sd = Stardust("http://core.test", transport=core)
    sd.record("openai", "gpt-4o", 1, 1)
    start = time.time()
    assert sd.flush(0.3) is False
    assert time.time() - start < 2
    assert sd.pending == 1
    sd.close(0.1)


def test_bounded_buffer_drops_oldest():
    core = FakeCore([urllib.error.URLError("down")] * 1000)
    sd = Stardust("http://core.test", transport=core, max_buffer=3)
    for i in range(6):
        sd.record("openai", f"m{i}", 1, 1)
    assert sd.dropped >= 2
    assert sd.pending <= 4  # 3 buffered + possibly 1 in flight
    sd.close(0.1)


def test_disabled_records_nothing(core):
    sd = Stardust("http://core.test", transport=core, enabled=False)
    sd.record("openai", "gpt-4o", 1, 1)
    assert sd.flush(1) and core.calls == 0
    sd.close(1)


def test_record_response_never_raises(stardust):
    class Weird:
        @property
        def usage(self):
            raise RuntimeError("boom")

    assert stardust.record_response(Weird(), provider="anthropic") is None
