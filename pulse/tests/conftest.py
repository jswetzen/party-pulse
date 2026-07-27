import pytest


@pytest.fixture(autouse=True)
def _use_plain_static_storage(settings):
    # whitenoise's CompressedManifestStaticFilesStorage (settings.py, for prod) requires a
    # manifest from `collectstatic`, which tests never run -- any test that renders a full page
    # (not just fragments) hits `{% static %}` and blows up with "Missing staticfiles manifest
    # entry" otherwise.
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
