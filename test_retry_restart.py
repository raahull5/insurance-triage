from pathlib import Path
from tempfile import mkdtemp
from types import SimpleNamespace

from src.core.resilience import OutcomeStatus, ProcessingOutcome
from src.ingestion.retry_queue import RetryQueue
from src.pipeline.autonomous_runner import AutonomousPipeline


# Create a fresh, isolated test directory.
test_dir = Path(mkdtemp(prefix="retry_restart_test_", dir="data"))
queue_path = test_dir / "retry_queue.json"
msg_id = "simulated-retry-001"


class FakeHimalaya:
    def read_message(self, message_id):
        assert message_id == msg_id
        return {
            "id": message_id,
            "from": [],
            "subject": "Simulated retry test",
            "body": "Test message",
        }


def make_pipeline(queue):
    pipeline = object.__new__(AutonomousPipeline)
    pipeline.retry_queue = queue
    pipeline._deferred = queue.pending
    pipeline.himalaya = FakeHimalaya()
    pipeline.mock_mode = True
    pipeline.allowed_senders = set()
    pipeline.health = SimpleNamespace(
        record_failure=lambda *args: None
    )
    return pipeline


# Phase 1: Simulate a transient processing failure.
print("\nPHASE 1: Simulating a transient failure")

queue1 = RetryQueue(queue_path)
pipeline1 = make_pipeline(queue1)

pipeline1.process_email = lambda raw: ProcessingOutcome(
    status=OutcomeStatus.FAILED_LLM,
    email_id=msg_id,
    detail="Simulated temporary AI failure",
)

outcome1 = pipeline1._process_one(
    msg_id, defer_on_failure=True
)

assert outcome1.status == OutcomeStatus.FAILED_LLM
assert queue1.pending == [msg_id]
assert msg_id in pipeline1._deferred
print("PASS: Failure recorded.")
print("PASS: Message persisted in retry queue.")


# Phase 2: Simulate an application restart.
print("\nPHASE 2: Simulating application restart")

queue2 = RetryQueue(queue_path)
pipeline2 = make_pipeline(queue2)

assert pipeline2._deferred == [msg_id]
print("PASS: Pending message restored after restart.")


# Phase 3: Simulate successful recovery.
print("\nPHASE 3: Simulating successful recovery")

pipeline2.process_email = lambda raw: ProcessingOutcome(
    status=OutcomeStatus.COMPLETED,
    email_id=msg_id,
    detail="Simulated successful recovery",
)

outcome2 = pipeline2._process_one(
    msg_id, defer_on_failure=True
)

assert outcome2.status == OutcomeStatus.COMPLETED
assert queue2.pending == []
assert pipeline2._deferred == []
assert RetryQueue(queue_path).pending == []

print("PASS: Message processed successfully.")
print("PASS: Message removed from in-memory queue.")
print("PASS: Message removed from persistent queue.")
print("\nALL ASSERTIONS PASSED")
print(f"Test artifacts: {test_dir.resolve()}")
