from unittest.mock import MagicMock

from psacc.psa.RemoteClient import RemoteClient
from psacc.car import Cars


def get_rc() -> RemoteClient:
    account_info = MagicMock()
    account_info.realm = ""
    return RemoteClient(account_info, Cars(), None, None)
