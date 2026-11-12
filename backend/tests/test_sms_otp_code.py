import unittest
from unittest.mock import MagicMock

from psacc.psa.RemoteClient import RemoteClient, RemoteException

from tests.utils import get_rc


def get_remote_client(status_code, text=""):
    remote_client: RemoteClient = get_rc()
    remote_client.manager = MagicMock()
    res = MagicMock()
    res.status_code = status_code
    res.ok = status_code < 400
    res.text = text
    remote_client.manager.post.return_value = res
    return remote_client


class TestSmsOtpCode(unittest.TestCase):

    def test_sms_request_accepted(self):
        # GIVEN PSA accepts the SMS request
        remote_client = get_remote_client(202)

        # WHEN an SMS code is requested
        res = remote_client.get_sms_otp_code()

        # THEN the response is returned without error
        self.assertEqual(202, res.status_code)

    def test_sms_request_rejected(self):
        # GIVEN PSA rejects the SMS request
        remote_client = get_remote_client(401, '{"error": "invalid_token"}')

        # WHEN an SMS code is requested
        # THEN an exception with status code and body is raised
        with self.assertRaises(RemoteException) as ctx:
            remote_client.get_sms_otp_code()
        self.assertIn("401", str(ctx.exception))
        self.assertNotIn("invalid_token", str(ctx.exception))  # status code only, never the response body

    def test_sms_request_rejected_logs_status_only(self):
        remote_client = get_remote_client(500, '{"secret": "SECRET-BODY"}')
        with self.assertLogs("psacc.psa.RemoteClient", level="INFO") as logs, self.assertRaises(RemoteException):
            remote_client.get_sms_otp_code()
        self.assertIn("500", "".join(logs.output))
        self.assertNotIn("SECRET-BODY", "".join(logs.output))


if __name__ == '__main__':
    unittest.main()
