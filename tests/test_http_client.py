import httpx
import pytest
import respx

from pricewatch.collectors.http import PoliteHttpClient, RobotsDisallowedError
from pricewatch.config import CollectionSettings

BASE = "https://shop.test"


def make_client(sleeps: list[float], **overrides: object) -> PoliteHttpClient:
    settings = CollectionSettings(
        **{"delay_seconds": 1.0, "max_retries": 2, "backoff_factor": 0.5, **overrides}
    )
    return PoliteHttpClient(settings, sleep=sleeps.append, clock=lambda: 0.0)


@respx.mock
def test_robots_disallow_blocks_request() -> None:
    respx.get(f"{BASE}/robots.txt").respond(200, text="User-agent: *\nDisallow: /private/\n")
    page = respx.get(f"{BASE}/private/x").respond(200)
    with make_client([]) as client:
        with pytest.raises(RobotsDisallowedError):
            client.get(f"{BASE}/private/x")
        assert client.is_allowed(f"{BASE}/public")
    assert not page.called


@respx.mock
def test_robots_404_allows_and_is_cached() -> None:
    robots = respx.get(f"{BASE}/robots.txt").respond(404)
    respx.get(f"{BASE}/a").respond(200, text="ok")
    with make_client([]) as client:
        assert client.get(f"{BASE}/a").text == "ok"
        client.get(f"{BASE}/a")
    assert robots.call_count == 1


@respx.mock
def test_robots_403_disallows_everything() -> None:
    respx.get(f"{BASE}/robots.txt").respond(403)
    with make_client([]) as client:
        assert not client.is_allowed(f"{BASE}/a")


@respx.mock
def test_robots_network_error_allows() -> None:
    respx.get(f"{BASE}/robots.txt").mock(side_effect=httpx.ConnectError("boom"))
    with make_client([], max_retries=0) as client:
        assert client.is_allowed(f"{BASE}/a")


@respx.mock
def test_user_agent_is_sent() -> None:
    respx.get(f"{BASE}/robots.txt").respond(404)
    route = respx.get(f"{BASE}/a").respond(200)
    with make_client([], user_agent="TestBot/1.0") as client:
        client.get(f"{BASE}/a")
    assert route.calls.last.request.headers["User-Agent"] == "TestBot/1.0"


@respx.mock
def test_retry_with_exponential_backoff() -> None:
    respx.get(f"{BASE}/robots.txt").respond(404)
    route = respx.get(f"{BASE}/a").mock(
        side_effect=[httpx.Response(503), httpx.Response(500), httpx.Response(200, text="ok")]
    )
    sleeps: list[float] = []
    with make_client(sleeps, delay_seconds=0) as client:
        assert client.get(f"{BASE}/a").text == "ok"
    assert route.call_count == 3
    assert sleeps == [0.5, 1.0]


@respx.mock
def test_retry_after_header_is_honoured() -> None:
    respx.get(f"{BASE}/robots.txt").respond(404)
    respx.get(f"{BASE}/a").mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200)]
    )
    sleeps: list[float] = []
    with make_client(sleeps, delay_seconds=0) as client:
        client.get(f"{BASE}/a")
    assert sleeps == [7.0]


@respx.mock
def test_invalid_retry_after_falls_back_to_backoff() -> None:
    respx.get(f"{BASE}/robots.txt").respond(404)
    respx.get(f"{BASE}/a").mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "soon"}), httpx.Response(200)]
    )
    sleeps: list[float] = []
    with make_client(sleeps, delay_seconds=0) as client:
        client.get(f"{BASE}/a")
    assert sleeps == [0.5]


@respx.mock
def test_retries_exhausted_raises_status_error() -> None:
    respx.get(f"{BASE}/robots.txt").respond(404)
    route = respx.get(f"{BASE}/a").respond(503)
    with make_client([], delay_seconds=0) as client, pytest.raises(httpx.HTTPStatusError):
        client.get(f"{BASE}/a")
    assert route.call_count == 3


@respx.mock
def test_transport_error_retried_then_raised() -> None:
    respx.get(f"{BASE}/robots.txt").respond(404)
    route = respx.get(f"{BASE}/a").mock(side_effect=httpx.ConnectTimeout("lent"))
    with make_client([], delay_seconds=0) as client, pytest.raises(httpx.ConnectTimeout):
        client.get(f"{BASE}/a")
    assert route.call_count == 3


@respx.mock
def test_client_error_is_not_retried() -> None:
    respx.get(f"{BASE}/robots.txt").respond(404)
    route = respx.get(f"{BASE}/missing").respond(404)
    with make_client([]) as client, pytest.raises(httpx.HTTPStatusError):
        client.get(f"{BASE}/missing")
    assert route.call_count == 1


@respx.mock
def test_throttle_and_crawl_delay() -> None:
    respx.get(f"{BASE}/robots.txt").respond(200, text="User-agent: *\nCrawl-delay: 3\n")
    respx.get(f"{BASE}/a").respond(200)
    sleeps: list[float] = []
    with make_client(sleeps, delay_seconds=1.0) as client:
        client.get(f"{BASE}/a")
        client.get(f"{BASE}/a")
    # robots.txt est lu juste avant la 1re page : les deux pages attendent le Crawl-delay (3 s).
    assert sleeps == [3.0, 3.0]


@respx.mock
def test_robots_can_be_ignored() -> None:
    robots = respx.get(f"{BASE}/robots.txt").respond(200, text="User-agent: *\nDisallow: /\n")
    respx.get(f"{BASE}/a").respond(200)
    with make_client([], respect_robots_txt=False) as client:
        client.get(f"{BASE}/a")
    assert not robots.called


def test_injected_client_is_not_closed() -> None:
    inner = httpx.Client()
    PoliteHttpClient(CollectionSettings(), client=inner).close()
    assert not inner.is_closed
    inner.close()
