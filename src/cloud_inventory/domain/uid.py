from urllib.parse import quote

from cloud_inventory.domain.models import Provider, Realm, ResourceType


def build_cloud_uid(
    provider: Provider,
    realm: Realm,
    account_id: str,
    region: str,
    resource_type: ResourceType,
    external_id: str,
) -> str:
    components = (
        provider.value,
        realm.value,
        account_id,
        region,
        resource_type.value,
        external_id,
    )
    return ":".join(quote(component, safe="-._~") for component in components)
