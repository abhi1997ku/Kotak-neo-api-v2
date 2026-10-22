from unittest.mock import Mock, patch

from neo_api_client.rest import RESTClientObject


def test_post_and_get_include_timeout():
    config = Mock()
    client = RESTClientObject(config)

    with patch.object(client._http_session(), "post") as post_mock:
        post_mock.return_value = Mock(status_code=200)
        client.request("POST", "https://example.com/test", headers={"Content-Type": "application/json"}, body={"a": 1})
        assert post_mock.call_args.kwargs["timeout"] == 15

    with patch.object(client._http_session(), "get") as get_mock:
        get_mock.return_value = Mock(status_code=200)
        client.request("GET", "https://example.com/test", headers={"Content-Type": "application/json"})
        assert get_mock.call_args.kwargs["timeout"] == 15


def test_transport_reuses_connections_without_automatic_order_retries():
    client = RESTClientObject(Mock())
    session = client._http_session()
    assert client._http_session() is session
    assert session.get_adapter("https://broker.example").max_retries.total == 0


def test_transport_sessions_are_not_shared_between_threads():
    from concurrent.futures import ThreadPoolExecutor
    client = RESTClientObject(Mock())
    session = client._http_session()
    with ThreadPoolExecutor(max_workers=1) as executor:
        other = executor.submit(client._http_session).result()
    assert other is not session
