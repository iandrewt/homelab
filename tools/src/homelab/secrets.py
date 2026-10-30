import asyncio
import atexit
import functools
import os
import re

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


# An item's fields by (section title, field title), as `read_items` returns and `write_item` takes
Fields = dict[tuple[str, str], str]


def read_items(vault: str, prefix: str) -> dict[str, Fields]:
    """Every item in the vault whose title starts with prefix, by title."""
    return _runner().run(_read_items(_client(), vault, prefix))


def write_item(vault: str, title: str, fields: Fields, *, concealed: set[str], tags: list[str]) -> str:
    """Create the Secure Note, or replace its fields: "created", "updated" or "unchanged". A field
    titled in `concealed` is hidden."""
    return _runner().run(_write_item(_client(), vault, title, fields, concealed, tags))


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
    resolved = []
    for reference in references:
        try:
            resolved.append(await client.secrets.resolve(reference))
        except Exception as exc:
            raise SecretError(f"1Password could not resolve {reference}: {exc}") from exc
    return resolved



async def _vault(client, title: str) -> str:
    for vault in await client.vaults.list():
        if vault.title == title:
            return vault.id
    raise SecretError(f"no 1Password vault titled {title}")


def _fields_of(item) -> Fields:
    sections = {s.id: s.title for s in item.sections}
    return {(sections.get(f.section_id, ""), f.title): f.value for f in item.fields}


async def _read_items(client, vault: str, prefix: str) -> dict[str, Fields]:
    vault_id = await _vault(client, vault)
    found = {}
    for overview in await client.items.list(vault_id):
        if overview.title.startswith(prefix):
            found[overview.title] = _fields_of(await client.items.get(vault_id, overview.id))
    return found


async def _write_item(client, vault: str, title: str, fields: Fields, concealed: set[str], tags: list[str]) -> str:
    from onepassword import (
        ItemCategory,
        ItemCreateParams,
        ItemField,
        ItemFieldType,
        ItemSection,
    )

    def slug(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")

    sections = [ItemSection(id=slug(s), title=s) for s in dict.fromkeys(s for s, _ in fields)]
    items = [
        ItemField(id=f"{slug(s)}.{slug(f)}", title=f, section_id=slug(s), value=value,
                  field_type=ItemFieldType.CONCEALED if f in concealed else ItemFieldType.TEXT)
        for (s, f), value in fields.items()
    ]
    vault_id = await _vault(client, vault)
    held = next((o for o in await client.items.list(vault_id) if o.title == title), None)
    if held is None:
        await client.items.create(ItemCreateParams(category=ItemCategory.SECURENOTE, vault_id=vault_id,
                                                   title=title, sections=sections, fields=items, tags=tags))
        return "created"
    item = await client.items.get(vault_id, held.id)
    if _fields_of(item) == fields:
        return "unchanged"
    item.sections, item.fields, item.tags = sections, items, tags
    await client.items.put(item)
    return "updated"
