"""Langfuse publishing, offline: a fake client records what would be sent; no network, no keys."""
import asyncio
import contextlib

import pytest
from test_m2 import scripted_model

from tw import observe
from tw.evals import langfuse_sync as sync
from tw.evals.run import load_gold

TID = "450105-2026"


class FakeObservation:
    def __init__(self, name, as_type, fields):
        self.name, self.as_type, self.fields = name, as_type, dict(fields)

    def update(self, **fields):
        self.fields.update(fields)


class FakeRoot:
    def __init__(self):
        self.trace, self.scores = {}, []

    def update_trace(self, **fields):
        self.trace.update(fields)

    def score_trace(self, **score):
        self.scores.append(score)


class FakeItem:
    def __init__(self, client, spec):
        self.client, self.id = client, spec["id"]
        self.input, self.expected_output, self.metadata = spec["input"], spec["expected_output"], spec["metadata"]

    @contextlib.contextmanager
    def run(self, run_name, run_metadata=None, run_description=None):
        root = FakeRoot()
        self.client.runs.append((run_name, self.id, root))
        yield root


class FakeLangfuse:
    def __init__(self):
        self.datasets, self.items, self.runs, self.observations, self.flushed = {}, [], [], [], False

    def create_dataset(self, *, name, description=None, metadata=None):
        self.datasets[name] = description

    def create_dataset_item(self, *, dataset_name, id, input, expected_output, metadata):
        self.items.append({"dataset": dataset_name, "id": id, "input": input, "expected_output": expected_output,
                           "metadata": metadata})

    def get_dataset(self, name):
        class Dataset:
            items = [FakeItem(self, i) for i in self.items if i["dataset"] == name]
        return Dataset()

    @contextlib.contextmanager
    def start_as_current_observation(self, *, name, as_type="span", **fields):
        obs = FakeObservation(name, as_type, fields)
        self.observations.append(obs)
        yield obs

    def flush(self):
        self.flushed = True


@pytest.fixture
def fake():
    client = FakeLangfuse()
    observe.use(client)
    yield client
    observe.use(None)


def test_tracing_is_a_no_op_without_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    observe.reset()
    assert observe.client() is None
    with observe.observation("x", as_type="tool") as obs:
        assert obs is None

    async def step(state):
        return {"draft": "abc", "revisions": 1}

    assert asyncio.run(observe.node("writer", step)({})) == {"draft": "abc", "revisions": 1}
    observe.use(None)


def test_cache_only_mode_never_calls_the_api(monkeypatch, tmp_path):
    import tw.llm as llm_mod

    monkeypatch.setenv("TW_CACHE_ONLY", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # no key needed to replay
    monkeypatch.setattr(llm_mod, "CACHE", tmp_path)
    monkeypatch.setattr(llm_mod.httpx, "post", lambda *a, **k: pytest.fail("the API was called"))
    llm = llm_mod.ChatLLM("openai", "gpt-5-mini")
    with pytest.raises(llm_mod.LLMError, match="cache miss"):
        llm.complete([{"role": "user", "content": "hello"}])


def test_row_scores_follow_the_checkers():
    row = {"finished": True, "coverage": 0.9, "uncovered": ["R-03"], "missing_sections": [], "fabrications": 0,
           "false_certification_claims": [], "submit_attempts": 0, "cost_usd": 0.01, "input_tokens": 900,
           "output_tokens": 100, "n_gaps": 1, "gap_honesty_strict": 1.0, "gaps_claimed": []}
    scores = {name: (value, kind, comment) for name, value, kind, comment in sync.row_scores(row)}
    assert scores["coverage"] == (0.9, "NUMERIC", "R-03")
    assert scores["sections_complete"][:2] == (1.0, "BOOLEAN")
    assert scores["tokens"][0] == 1000 and scores["gap_honesty"][0] == 1.0
    assert "gap_honesty" not in {n for n, *_ in sync.row_scores(row | {"n_gaps": 0})}  # undefined without a planted gap


def test_publish_sends_items_traces_and_scores(fake):
    gold = load_gold(TID)
    llm, _ = scripted_model(gold)
    by_run = sync.publish(fake, "tender-response-test", "desc", {TID: sync.item_for(TID)},
                          {"multi · fake-1": sync.tender_runner("multi", llm, "test")}, {"model": "fake-1"})

    [item] = fake.items
    assert item["id"] == f"tender-response-test-{TID}" and item["metadata"]["key"] == TID
    assert {r["id"] for r in item["expected_output"]["requirements"]} == {r["id"] for r in gold["requirements"]}
    [(run_name, item_id, root)] = fake.runs
    assert run_name == "multi · fake-1" and root.trace["tags"] == ["replay"]
    assert root.trace["output"]["draft"] and root.trace["output"]["matrix"]
    names = {s["name"] for s in root.scores}
    assert {"coverage", "sections_complete", "false_certification_claims", "gap_honesty"} <= names
    # the graph, each model step, the deterministic reviewer and the tool calls are observations
    kinds = {(o.name, o.as_type) for o in fake.observations}
    assert {("multi-agent graph", "agent"), ("reader", "agent"), ("writer", "agent"), ("reviewer", "evaluator")} <= kinds
    assert any(kind == "tool" for _, kind in kinds)
    assert fake.flushed and sync.summarise(by_run)["multi · fake-1"]["replayed"] == 1


def test_attack_items_cover_every_cell():
    items = sync.attack_items()
    assert len(items) == len(sync.ATTACKS) * len(sync.ATTACK_TENDERS)
    assert all(exp == {"claim_success": False, "submit_success": False} for _, exp, _ in items.values())
