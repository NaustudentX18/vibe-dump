from vibedump.agent_pipeline import FakeAgentPipeline
from vibedump.database import Database
from vibedump.schemas import validate_blueprint


def test_fake_pipeline_stores_transcript_blueprint_and_memory(tmp_path):
    db = Database(tmp_path / "pipeline.sqlite3")
    db.initialize()
    result = FakeAgentPipeline(db).ingest_fake_dump("Robot Todo", "idea.wav")

    assert result.dump_id > 0
    assert "idea.wav" in result.transcript
    assert validate_blueprint(result.blueprint) == []
    assert db.search_chunks("Robot") or db.search_chunks("transcript")
    db.close()
