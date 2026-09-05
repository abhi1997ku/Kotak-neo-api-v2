from unittest.mock import Mock, patch

from neo_api_client.rest import RESTClientObject


def test_post_and_get_include_timeout():
    config = Mock()
    client = RESTClientObject(config)

    with patch("neo_api_client.rest.requests.post") as post_mock:
        post_mock.return_value = Mock(status_code=200)
        client.request("POST", "https://example.com/test", headers={"Content-Type": "application/json"}, body={"a": 1})
        assert post_mock.call_args.kwargs["timeout"] == 15

    with patch("neo_api_client.rest.requests.get") as get_mock:
        get_mock.return_value = Mock(status_code=200)
        client.request("GET", "https://example.com/test", headers={"Content-Type": "application/json"})
        assert get_mock.call_args.kwargs["timeout"] == 15
