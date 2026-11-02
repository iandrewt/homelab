"""Mails the trail's guardrail events to the findings topic, each with who, what and where."""

import base64
import functools
import gzip
import json
import os
import urllib.parse

import boto3
import botocore.exceptions

TOPIC = os.environ["TOPIC"]
REGION = os.environ["AWS_REGION"]
REQUEST_LIMIT = 2000


def handler(event, context):
    batch = json.loads(gzip.decompress(base64.b64decode(event["awslogs"]["data"])))
    if batch["messageType"] != "DATA_MESSAGE":
        return
    trail = [json.loads(e["message"]) for e in batch["logEvents"]]
    sections = [describe(record, link(batch["logGroup"], batch["logStream"], record["eventID"])) for record in trail]
    first = trail[0]
    more = f", and {len(trail) - 1} more" if len(trail) > 1 else ""
    subject = f"Guardrails: {first['eventName']} by {who(first)['short']} in {account(first)}{more}"
    boto3.client("sns").publish(TopicArn=TOPIC, Subject=subject[:100], Message="\n\n".join(sections)[:250_000])


def who(record: dict) -> dict:
    identity = record.get("userIdentity", {})
    full = identity.get("arn") or identity.get("invokedBy") or identity.get("principalId") or identity.get("type", "?")
    # An SSO session's ARN ends in its user's name
    return {"full": f"{full} ({identity.get('type', '?')})", "short": full.rsplit("/", 1)[-1]}


@functools.cache
def account_name(number: str) -> str | None:
    # A mail with a number is better than none
    try:
        return boto3.client("organizations").describe_account(AccountId=number)["Account"]["Name"]
    except botocore.exceptions.ClientError:
        return None


def account(record: dict) -> str:
    number = record.get("recipientAccountId", "?")
    name = account_name(number) if number.isdigit() else None
    return f"{name} ({number})" if name else number


def describe(record: dict, url: str) -> str:
    failed = record.get("errorCode")
    result = f"{failed}: {record.get('errorMessage', '')}" if failed else "succeeded"
    request = json.dumps(record.get("requestParameters"), separators=(",", ":"))
    if len(request) > REQUEST_LIMIT:
        request = request[:REQUEST_LIMIT] + "..."
    return "\n".join([
        f"{record['eventSource']} {record['eventName']}, at {record['eventTime']}",
        f"By:      {who(record)['full']}",
        f"In:      {account(record)}, {record.get('awsRegion', '?')}",
        f"From:    {record.get('sourceIPAddress', '?')}, {record.get('userAgent', '?')}",
        f"Result:  {result}",
        f"Request: {request}",
        f"Event:   {url}",
    ])


def link(group: str, stream: str, event_id: str) -> str:
    """The console's fragment holds a URL of its own, encoded twice and with $ for %."""
    def encode(s: str) -> str:
        return urllib.parse.quote(urllib.parse.quote(s, safe=""), safe="").replace("%", "$")

    return (f"https://{REGION}.console.aws.amazon.com/cloudwatch/home?region={REGION}#logsV2:log-groups"
            f"/log-group/{encode(group)}/log-events/{encode(stream)}$3FfilterPattern$3D{encode(event_id)}")
