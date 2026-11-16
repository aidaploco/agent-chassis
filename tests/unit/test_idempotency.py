from agent_chassis.packs import idempotency_key


def test_no_key_outside_an_activity():
    # In-workflow (pure) tools and plain unit tests have no activity to key on.
    assert idempotency_key() is None
