import asyncio
import atexit
import functools
import os

INTEGRATION_NAME = "homelab"
INTEGRATION_VERSION = "v1.0.0"

TOKEN_ENV = "OP_SERVICE_ACCOUNT_TOKEN"
ACCOUNT_ENV = "OP_ACCOUNT"


class SecretError(Exception):
    pass


def resolve(*references: str) -> list[str]:
    client = _client()
    return _runner().run(_resolve(client, references))


def resolve_one(reference: str) -> str:
    return resolve(reference)[0]


@functools.cache
def _runner() -> asyncio.Runner:
    runner = asyncio.Runner()
    atexit.register(runner.close)
    return runner


@functools.cache
def _client():
    from onepassword.client import Client

    auth = _auth()
    try:
        return _runner().run(
            Client.authenticate(
                auth=auth,
                integration_name=INTEGRATION_NAME,
                integration_version=INTEGRATION_VERSION,
            )
        )
    except Exception as exc:
        raise SecretError(f"1Password did not authorise this client: {exc}") from exc


def _auth():
    if token := os.environ.get(TOKEN_ENV):
        return token

    if account := os.environ.get(ACCOUNT_ENV):
        from onepassword.client import DesktopAuth

        return DesktopAuth(account_name=account)

    raise SecretError(
        f"no 1Password credentials: set {TOKEN_ENV} to a service account token, or "
        f"{ACCOUNT_ENV} to an account name to authorise through the desktop app."
    )


async def _resolve(client, references) -> list[str]:
    try:
        return [await client.secrets.resolve(reference) for reference in references]
    except Exception as exc:
        raise SecretError(f"1Password could not resolve a reference: {exc}") from exc

