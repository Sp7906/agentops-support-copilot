import os
import tempfile

# Tests are offline and isolated: temp database, no Azure calls, even if a .env exists.
os.environ["AZURE_OPENAI_ENDPOINT"] = ""
os.environ["AZURE_OPENAI_API_KEY"] = ""
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    from app import db
    db.init(reset=True)
    yield
