"""Tests for the HTTP layer: versioning rules, publishing gates, and the error
responses the frontend depends on."""

VALID_DEFINITION = {
    "instructions": "Look up the order and reply with a short decision about it.",
    "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}},
    "output_schema": {"type": "object", "properties": {"answer": {"type": "string"}}},
    "examples": [],
    "allowed_tools": ["record_lookup"],
    "approval_required_tools": [],
    "max_steps": 6,
}


def create_skill(client, name="My Skill"):
    response = client.post("/api/skills", json={"name": name, "purpose": "testing"})
    assert response.status_code == 201
    return response.json()


# --- skills ------------------------------------------------------------------


def test_creating_a_skill_also_creates_a_draft(client):
    body = create_skill(client)
    assert body["draft_version_id"]

    detail = client.get(f"/api/skills/{body['skill']['id']}").json()
    assert len(detail["versions"]) == 1
    assert detail["versions"][0]["status"] == "draft"


def test_duplicate_skill_names_are_rejected(client):
    create_skill(client, "Same Name")
    response = client.post("/api/skills", json={"name": "Same Name", "purpose": ""})
    assert response.status_code == 409


def test_missing_skill_returns_404(client):
    assert client.get("/api/skills/9999").status_code == 404


# --- publishing --------------------------------------------------------------


def test_an_invalid_draft_cannot_be_published(client):
    draft_id = create_skill(client)["draft_version_id"]

    client.put(
        f"/api/versions/{draft_id}",
        json={**VALID_DEFINITION, "allowed_tools": ["create_task"], "approval_required_tools": []},
    )

    response = client.post(f"/api/versions/{draft_id}/publish")

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["problems"]
    assert any(p["field"] == "approval_required_tools" for p in detail["problems"])


def test_a_valid_draft_publishes_and_then_freezes(client):
    draft_id = create_skill(client)["draft_version_id"]
    client.put(f"/api/versions/{draft_id}", json=VALID_DEFINITION)

    published = client.post(f"/api/versions/{draft_id}/publish")
    assert published.status_code == 200
    assert published.json()["version"]["status"] == "published"

    # A published version can never be edited again.
    edit = client.put(f"/api/versions/{draft_id}", json=VALID_DEFINITION)
    assert edit.status_code == 409

    # Nor published twice.
    assert client.post(f"/api/versions/{draft_id}/publish").status_code == 409


def test_saving_an_invalid_draft_is_allowed_but_reports_problems(client):
    """Half-finished work must be saveable; publishing is where rules bite."""
    draft_id = create_skill(client)["draft_version_id"]

    response = client.put(f"/api/versions/{draft_id}", json={**VALID_DEFINITION, "instructions": ""})

    assert response.status_code == 200
    assert any(p["field"] == "instructions" for p in response.json()["problems"])


# --- version lifecycle -------------------------------------------------------


def test_only_one_draft_at_a_time(client):
    body = create_skill(client)
    skill_id = body["skill"]["id"]

    response = client.post(f"/api/skills/{skill_id}/versions")
    assert response.status_code == 409


def test_new_draft_copies_the_previous_version(client):
    body = create_skill(client)
    skill_id, draft_id = body["skill"]["id"], body["draft_version_id"]

    client.put(f"/api/versions/{draft_id}", json=VALID_DEFINITION)
    client.post(f"/api/versions/{draft_id}/publish")

    new_draft = client.post(f"/api/skills/{skill_id}/versions").json()

    assert new_draft["version_number"] == 2
    assert new_draft["status"] == "draft"
    assert new_draft["instructions"] == VALID_DEFINITION["instructions"]


def test_published_versions_cannot_be_deleted(client):
    body = create_skill(client)
    draft_id = body["draft_version_id"]

    client.put(f"/api/versions/{draft_id}", json=VALID_DEFINITION)
    client.post(f"/api/versions/{draft_id}/publish")

    assert client.delete(f"/api/versions/{draft_id}").status_code == 409


# --- comparison --------------------------------------------------------------


def test_comparing_two_versions_reports_what_changed(client):
    body = create_skill(client)
    skill_id, draft_id = body["skill"]["id"], body["draft_version_id"]

    client.put(f"/api/versions/{draft_id}", json=VALID_DEFINITION)
    client.post(f"/api/versions/{draft_id}/publish")

    second = client.post(f"/api/skills/{skill_id}/versions").json()
    client.put(
        f"/api/versions/{second['id']}",
        json={**VALID_DEFINITION, "max_steps": 12, "instructions": "Completely new instructions."},
    )

    comparison = client.get(
        f"/api/skills/{skill_id}/compare", params={"left": draft_id, "right": second["id"]}
    ).json()

    changed = {d["field"] for d in comparison["differences"] if d["changed"]}
    assert changed == {"max_steps", "instructions"}
    assert comparison["changed_count"] == 2


def test_cannot_compare_versions_from_different_skills(client):
    first = create_skill(client, "Skill A")
    second = create_skill(client, "Skill B")

    response = client.get(
        f"/api/skills/{first['skill']['id']}/compare",
        params={"left": first["draft_version_id"], "right": second["draft_version_id"]},
    )
    assert response.status_code == 400


# --- running ----------------------------------------------------------------


def test_running_a_skill_with_errors_is_refused(client):
    draft_id = create_skill(client)["draft_version_id"]
    client.put(f"/api/versions/{draft_id}", json={**VALID_DEFINITION, "instructions": ""})

    response = client.post(f"/api/versions/{draft_id}/run", json={"input_data": {}})

    assert response.status_code == 422
    assert "errors" in response.json()["detail"]["message"]


def test_input_is_validated_before_the_model_is_called(client):
    draft_id = create_skill(client)["draft_version_id"]
    client.put(
        f"/api/versions/{draft_id}",
        json={
            **VALID_DEFINITION,
            "input_schema": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        },
    )

    response = client.post(f"/api/versions/{draft_id}/run", json={"input_data": {"order_id": 42}})

    assert response.status_code == 422
    assert "order_id" in response.json()["detail"]["message"]


# --- misc --------------------------------------------------------------------


def test_health_reports_configuration(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert "model_chain" in body


def test_tools_endpoint_marks_write_tools(client):
    tools = client.get("/api/tools").json()
    writes = [t["name"] for t in tools if t["is_write"]]
    assert writes == ["create_task"]


def test_unknown_execution_returns_404(client):
    assert client.get("/api/executions/4242").status_code == 404
