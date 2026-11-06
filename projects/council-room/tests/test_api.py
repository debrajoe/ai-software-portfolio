from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from council_chamber.app import create_app
from council_chamber.auth import Principal
from council_chamber.config import Settings


class FakeVerifier:
    principals = {
        "owner-mfa": Principal("owner-1", "Council Owner", "owner", True),
        "owner-no-mfa": Principal("owner-1", "Council Owner", "owner", False),
        "owner-as-agent": Principal("owner-1", "Council Owner", "agent_member", False),
        "jupiter": Principal("agent-jupiter", "Jupiter", "agent_member", False),
        "mercury": Principal("agent-mercury", "Mercury", "agent_member", False),
        "outsider": Principal("agent-outsider", "Outsider", "agent_member", False),
    }

    def verify(self, token: str) -> Principal:
        try:
            principal = self.principals[token]
        except KeyError as exc:
            raise HTTPException(status_code=401, detail="Invalid token") from exc
        if principal.role == "owner" and not principal.mfa_authenticated:
            raise HTTPException(status_code=403, detail="Owner MFA is required")
        return principal


@pytest.fixture()
def client(tmp_path: Path):
    settings = Settings(
        database_path=str(tmp_path / "council.db"),
        oidc_issuer="https://issuer.test/",
        oidc_audience="council-test",
        oidc_jwks_url="https://issuer.test/jwks",
        owner_subject="owner-1",
    )
    with TestClient(create_app(settings, FakeVerifier())) as test_client:
        yield test_client


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_chamber(client: TestClient, name="Council Room") -> str:
    response = client.post("/v1/chambers", json={"name": name}, headers=auth("owner-mfa"))
    assert response.status_code == 201
    return response.json()["id"]


def add_agent(client: TestClient, chamber_id: str, token: str, name: str):
    subject = FakeVerifier.principals[token].subject
    response = client.post(
        f"/v1/chambers/{chamber_id}/participants",
        json={"subject": subject, "display_name": name, "role": "agent_member", "kind": "agent"},
        headers=auth("owner-mfa"),
    )
    assert response.status_code == 201


def test_messages_are_saved_and_retrieved_in_order(client: TestClient):
    chamber_id = create_chamber(client)
    add_agent(client, chamber_id, "jupiter", "Jupiter")

    first = client.post(
        f"/v1/chambers/{chamber_id}/messages",
        json={"body": "First message", "client_message_id": "one"},
        headers=auth("owner-mfa"),
    )
    second = client.post(
        f"/v1/chambers/{chamber_id}/messages",
        json={"body": "Second message", "client_message_id": "two"},
        headers=auth("jupiter"),
    )
    assert first.status_code == second.status_code == 201

    history = client.get(f"/v1/chambers/{chamber_id}/messages", headers=auth("owner-mfa"))
    assert history.status_code == 200
    messages = history.json()["messages"]
    assert [message["body"] for message in messages] == ["First message", "Second message"]
    assert [message["participant"]["display_name"] for message in messages] == ["Council Owner", "Jupiter"]
    assert messages[0]["sequence"] < messages[1]["sequence"]


def test_messages_survive_new_app_instance(tmp_path: Path):
    path = str(tmp_path / "persistent.db")
    settings = Settings(path, "https://issuer.test/", "council-test", "https://issuer.test/jwks", "owner-1")
    first_client = TestClient(create_app(settings, FakeVerifier()))
    chamber_id = create_chamber(first_client, "Persistent Council")
    first_client.post(
        f"/v1/chambers/{chamber_id}/messages",
        json={"body": "Stored on disk"}, headers=auth("owner-mfa"),
    )
    first_client.close()

    second_client = TestClient(create_app(settings, FakeVerifier()))
    response = second_client.get(f"/v1/chambers/{chamber_id}/messages", headers=auth("owner-mfa"))
    assert response.status_code == 200
    assert response.json()["messages"][0]["body"] == "Stored on disk"
    second_client.close()


def test_unauthorized_user_cannot_read_or_write(client: TestClient):
    chamber_id = create_chamber(client)
    read = client.get(f"/v1/chambers/{chamber_id}/messages", headers=auth("outsider"))
    write = client.post(
        f"/v1/chambers/{chamber_id}/messages",
        json={"body": "Intrusion"}, headers=auth("outsider"),
    )
    assert read.status_code == 404
    assert write.status_code == 404


def test_owner_requires_mfa(client: TestClient):
    response = client.post("/v1/chambers", json={"name": "Denied"}, headers=auth("owner-no-mfa"))
    assert response.status_code == 403


def test_owner_subject_cannot_bypass_mfa_with_agent_role(client: TestClient):
    chamber_id = create_chamber(client)
    sent = client.post(
        f"/v1/chambers/{chamber_id}/messages",
        json={"body": "Owner confidential message"},
        headers=auth("owner-mfa"),
    )
    assert sent.status_code == 201

    read = client.get(
        f"/v1/chambers/{chamber_id}/messages",
        headers=auth("owner-as-agent"),
    )
    write = client.post(
        f"/v1/chambers/{chamber_id}/messages",
        json={"body": "MFA bypass attempt"},
        headers=auth("owner-as-agent"),
    )

    assert read.status_code == 403
    assert write.status_code == 403


def test_participant_cannot_impersonate_another_sender(client: TestClient):
    chamber_id = create_chamber(client)
    add_agent(client, chamber_id, "jupiter", "Jupiter")
    add_agent(client, chamber_id, "mercury", "Mercury")

    response = client.post(
        f"/v1/chambers/{chamber_id}/messages",
        json={"body": "Signed by verified identity"}, headers=auth("jupiter"),
    )
    assert response.status_code == 201
    assert response.json()["participant"]["display_name"] == "Jupiter"

