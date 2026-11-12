from unittest.mock import MagicMock

from psacc.psa.RemoteClient import RemoteClient
from psacc.psa.connected_car_api import Vehicles


def get_rc() -> RemoteClient:
    account_info = MagicMock()
    account_info.realm = ""
    return RemoteClient(account_info, Vehicles, None, None)
