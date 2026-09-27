"""Import smoke test for the worker scaffold (no broker connection required)."""


def test_broker_and_actors_import() -> None:
    from worker import actors, broker

    assert callable(broker.get_broker)
    assert actors.example_ping.actor_name == "example_ping"
