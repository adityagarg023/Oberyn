from __future__ import annotations


import httpx
import pytest

from workers.alerts import ResendNotifier, SlackWebhookNotifier


@pytest.mark.asyncio
async def test_resend_notifier_posts_email_to_mocked_provider() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "email-test-id"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notifier = ResendNotifier(
            "re_test_key",
            sender="Oberyn <alerts@example.test>",
            client=client,
        )
        await notifier.send(
            {"title": "New issue", "message": "A new issue was created"},
            "oncall@example.test",
        )

    assert len(requests) == 1
    assert requests[0].url == "https://api.resend.com/emails"
    assert requests[0].headers["Authorization"] == "Bearer re_test_key"
    assert b"oncall@example.test" in requests[0].content


@pytest.mark.asyncio
async def test_slack_notifier_posts_message_to_mocked_webhook() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="ok")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notifier = SlackWebhookNotifier(client=client)
        await notifier.send(
            {"title": "Regression", "message": "Issue regressed"},
            "https://hooks.slack.com/services/T000/B000/test",
        )

    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert b"Regression" in requests[0].content
    assert b"Issue regressed" in requests[0].content
