import sys
from pathlib import Path

import pytest
from django import VERSION
from django.conf import settings

from django_oso.oso import reset_oso


def pytest_configure():
    # Add test apps to sys path
    test_app = Path(__file__).parent
    sys.path.append(test_app.as_posix())

    settings.configure(
        INSTALLED_APPS=[
            "test_app",
            "test_app2",
            "django_oso",
            "django.contrib.auth",
            "django.contrib.contenttypes",
        ],
        ROOT_URLCONF="test_urls",
        DATABASES={
            "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
        },
    )


def negated_condition(variable):
    return f"NOT {variable}" if VERSION >= (3, 1) else f"{variable} = False"


def parenthesize(variable):
    return variable if VERSION >= (3,) else f"({variable})"


def is_true():
    return "" if VERSION >= (3, 1) else " = True"


@pytest.fixture
def load_additional_str(request):
    def _load_additional_str(string: str):
        from os import fdopen, remove
        from tempfile import mkstemp

        policy_dir = Path(__file__).parent / "test_app/policy"
        fd, path = mkstemp(suffix=".polar", dir=policy_dir)
        with fdopen(fd, "w") as tmp:
            tmp.write(string)
        request.addfinalizer(lambda: remove(path))
        reset_oso()

    return _load_additional_str
