import uuid

import pytest

pytestmark = pytest.mark.django_db


def test_questionnaire_redirects_when_respondent_missing(client):
    missing_id = uuid.uuid4()

    response = client.get(f"/r/{missing_id}/")

    assert response.status_code == 302
    assert response.url == f"/?stale={missing_id}"


def test_questionnaire_redirect_target_is_reachable(client):
    missing_id = uuid.uuid4()

    response = client.get(f"/r/{missing_id}/", follow=True)

    assert response.status_code == 200
    assert response.redirect_chain == [(f"/?stale={missing_id}", 302)]
