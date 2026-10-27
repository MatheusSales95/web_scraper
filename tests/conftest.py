import pytest

from fake_site import FakeSite


@pytest.fixture
def site():
    """Blog falso rodando numa porta livre durante o teste."""
    server = FakeSite().start_in_background()
    yield server
    server.shutdown()
    server.server_close()
