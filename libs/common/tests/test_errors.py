from common.errors import BudgetExceeded, DomainError, ErrorCode, NotFound, UpstreamTimeout


def test_envelope_matches_documented_shape() -> None:
    env = NotFound("idea not found", {"idea_id": "i1"}).to_envelope("req_9")
    assert env.model_dump(mode="json") == {
        "error": {
            "code": "NOT_FOUND",
            "message": "idea not found",
            "details": {"idea_id": "i1"},
            "request_id": "req_9",
        }
    }


def test_status_codes() -> None:
    assert NotFound("x").status_code == 404
    assert BudgetExceeded("x").status_code == 402
    assert UpstreamTimeout("x").status_code == 504
    assert DomainError("x").code is ErrorCode.INTERNAL
