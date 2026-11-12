from psacc.car import Car
from psacc.psa_client import PSAClient


def test_to_dict_from_dict_roundtrip():
    client = PSAClient("rt", "cid", "secret", "rrt", "AP-123", "clientsB2CPeugeot", "PT", "AP")
    client.vehicles_list.add(Car("VR3UHZKX", "vid", "Peugeot"))

    data = client.to_dict()
    restored = PSAClient.from_dict(data)

    assert restored.to_dict() == data
    assert restored.manager.refresh_token == "rt"
    assert restored.remote_client.remoteCredentials.refresh_token == "rrt"
    assert restored.vehicles_list.get_car_by_vin("VR3UHZKX").vehicle_id == "vid"
    assert restored.remote_client.vehicles_list is restored.vehicles_list
