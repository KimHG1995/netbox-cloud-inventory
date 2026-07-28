from cloud_inventory.domain.models import Provider, Realm, ResourceType
from cloud_inventory.domain.uid import build_cloud_uid


def test_cloud_uid_is_stable_and_escapes_reserved_characters() -> None:
    assert (
        build_cloud_uid(
            provider=Provider.NCP,
            realm=Realm.GOVERNMENT,
            account_id="account:01",
            region="KR",
            resource_type=ResourceType.VIRTUAL_MACHINE,
            external_id="server/123",
        )
        == "ncp:government:account%3A01:KR:virtual_machine:server%2F123"
    )


def test_cloud_uid_encodes_every_component() -> None:
    assert (
        build_cloud_uid(
            provider=Provider.AWS,
            realm=Realm.COMMERCIAL,
            account_id="account one",
            region="global/us",
            resource_type=ResourceType.OBJECT_BUCKET,
            external_id="bucket:name",
        )
        == "aws:commercial:account%20one:global%2Fus:object_bucket:bucket%3Aname"
    )
