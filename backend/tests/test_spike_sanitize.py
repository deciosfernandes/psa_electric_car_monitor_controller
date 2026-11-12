import json

from psacc.spike_report import sanitize

SECRETS = {"VF3REALVIN0000001": "VIN", "veh-id-777": "VEHICLE_ID", "AP-cust-999": "CUSTOMER_ID"}
LEAKS = ["VF3REALVIN0000001", "veh-id-777", "AP-cust-999", "tok-abc", "38.7223", "-27.2201", "37.74", "-25.67"]


def test_sanitize_rest_and_mqtt_shapes():
    rest = {
        "_links": {"self": {"href": "/user/vehicles/veh-id-777/status"}},
        "lastPosition": {"geometry": {"type": "Point", "coordinates": [-25.67, 37.74, 10]}, "properties": {}},
        "energy": [{"type": "Electric", "charging": {"nextDelayedTime": "PT22H30M", "status": "InProgress"}}],
        "vin": "VF3REALVIN0000001",
    }
    mqtt = {
        "vin": "VF3REALVIN0000001", "customer_id": "AP-cust-999", "access_token": "tok-abc",
        "VF3REALVIN0000001": {"note": "vin as key"},
        "position": {"latitude": 38.7223, "longitude": -27.2201},
        "nested": {"gps": {"lat": 38.7223, "lon": -27.2201}, "msg": "car VF3REALVIN0000001"},
        "precond_state": {"air_conditioning": {"status": "Enabled"}},
    }
    out = json.dumps(sanitize({"rest": rest, "mqtt": mqtt}, SECRETS))
    for leak in LEAKS:
        assert leak not in out
    assert "PT22H30M" in out and "Enabled" in out  # useful data survives
    assert "/user/vehicles/VEHICLE_ID/status" in out


def test_sanitize_location_like_keys():
    data = {
        "lastLocation": {"name": "Casa"},
        "GPS_fix": "38.1,-27.1",
        "homeAddress": "Rua X 1",
        "latLng": [38.1, -27.1],
        "LATLNG": "38.1,-27.1",
        "vinNumber": "VF3OTHERVIN000002",
        "VINNumber": "VF3OTHERVIN000002",
        "charging": {"status": "InProgress"},
    }
    out = sanitize(data, {})
    assert out["charging"] == {"status": "InProgress"}
    for key in data:
        if key != "charging":
            assert out[key] == "REDACTED", key


def test_program_hour_ahead_rounds_up_in_local_time():
    from datetime import UTC, datetime

    from psacc.settings import TZ
    from psacc.spike_report import program_hour_ahead

    now = datetime(2026, 11, 12, 12, 0, 30, tzinfo=UTC)  # 11h00:30 in the Azores (UTC-1 in winter)
    assert program_hour_ahead(now, TZ) == (11, 3)
    assert program_hour_ahead(now, UTC) == (12, 3)
    assert program_hour_ahead(datetime(2026, 11, 12, 0, 58, tzinfo=UTC), UTC) == (1, 0)
