import json
import logging
import threading
from datetime import datetime, timezone
from os import environ
from typing import Callable, Optional

import paho.mqtt.client as mqtt
from requests import RequestException

from psacc.car import Cars
from psacc.psa.AccountInformation import AccountInformation
from psacc.psa.RemoteCredentials import RemoteCredentials
from psacc.psa.constants import DEFAULT_PRECONDITIONING_PROGRAM, DELAYED_CHARGE, REMOTE_URL
from psacc.psa.mqtt_request import MQTTRequest
from psacc.psa.oauth import OpenIdCredentialManager
from psacc.utils import RateLimitException, rate_limit, TIMEOUT_IN_S
from psacc.psa.otp.otp import ConfigException, save_otp, load_otp

logger = logging.getLogger(__name__)

# mqtt reports the lock state as an int, unlike the rest api's DoorsState.lockedState enum.
# Only these two values have been observed on a real car (an Opel Mokka Electric, confirmed
# against the /Doors command responses).
MQTT_DOORS_LOCKING_STATE = {1: "Locked", 3: "Unlocked"}
MQTT_SERVER = "mwa.mpsa.com"
MQTT_RESP_TOPIC = "psa/RemoteServices/to/cid/"
MQTT_EVENT_TOPIC = "psa/RemoteServices/events/MPHRTServices/"
MQTT_TOKEN_TTL = 890
# minimum delay between two remote token refresh attempts: the broker closes the socket without
# CONNACK when the access token is expired, and paho reconnects at least every
# MAX_REFRESH_DELAY seconds, so refreshing on every disconnect would burn the
# refresh_token_now() rate limit (6 per 30 min) during a mere network outage.
MAX_REFRESH_DELAY = 120
# The remote refresh token's server-side lifetime is about 24h: a grant attempted 24h
# (plus a few seconds) after the previous one fails with 'invalid_grant', and each grant
# rotates the token. The __keep_mqtt timer is the ONLY periodic refresh on an idle car,
# so it must run well within that lifetime: 8h keeps the chain rotating forever and
# survives one missed cycle.
KEEPALIVE_PERIOD = 3600 * 8


class RemoteException(Exception):
    pass


def parse_event_date(value) -> datetime:
    """UTC timestamp of an mqtt event ('2022-03-30T12:00:52Z'), now when missing or malformed."""
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


class RemoteClient:

    def __init__(self, account_info: AccountInformation, vehicles_list: Cars, manager: OpenIdCredentialManager,
                 remoteCredentials: RemoteCredentials):
        self.vehicles_list = vehicles_list
        self.remoteCredentials: RemoteCredentials = remoteCredentials
        self.manager = manager
        self.precond_programs = {}
        self.lock_state = {}
        self.lock_state_at: dict[str, datetime] = {}  # when mqtt last reported lock_state (UTC)
        self.account_info = account_info
        self.headers = {
            "x-introspect-realm": self.account_info.realm,
            "accept": "application/hal+json",
            "User-Agent": "okhttp/4.8.0",
        }
        self.last_request = None
        self.mqtt_client = None
        self.otp = None
        self._lock = threading.Lock()
        self.update_thread: threading.Timer = None
        self.remote_token_last_update: Optional[datetime] = None
        # True while the remote token / OTP session is unusable (the user must redo the login)
        self.auth_failed = False
        # called with each decoded vehicle event, on the paho network thread: callbacks must not block
        self.on_event: list[Callable[[dict], None]] = []

    def __on_mqtt_connect(self, client, userdata, result_code, _):  # pylint: disable=unused-argument
        logger.info("Connected with result code %s", result_code)
        topics = [MQTT_RESP_TOPIC + self.account_info.get_mqtt_customer_id() + "/#"]
        for car in self.vehicles_list:
            topics.append(MQTT_EVENT_TOPIC + car.vin)
        for topic in topics:
            client.subscribe(topic)
            logger.info("subscribe to %s", topic)

    def _on_mqtt_disconnect(self, client, userdata, result_code):  # pylint: disable=unused-argument
        logger.warning("Disconnected with result code %d", result_code)
        if result_code == 1:
            self._refresh_remote_token(force=True)
        elif result_code == 7:
            # the broker closes the socket without CONNACK when the access token is expired:
            # paho then retries forever with the same stale token unless it is refreshed here.
            # Throttling is handled inside _refresh_remote_token.
            self._refresh_remote_token(force=True)
        else:
            logger.warning(mqtt.error_string(result_code))

    def _on_mqtt_message(self, client, userdata, msg):  # pylint: disable=unused-argument
        try:
            logger.info("mqtt msg received: %s %s", msg.topic, msg.payload)
            data = json.loads(msg.payload)
            if msg.topic.startswith(MQTT_RESP_TOPIC):
                if "return_code" not in data:
                    logger.debug("mqtt msg hasn't return code")
                elif data["return_code"] == "400":
                    self._refresh_remote_token(force=True)
                    if self.last_request:
                        logger.warning("last request is send again, token was expired")
                        last_request = self.last_request
                        self.last_request = None
                        self.publish(last_request, store=False)
                    else:
                        logger.error("Last request might have been send twice without success")
                elif data["return_code"] != "0":
                    logger.error('mqtt error %s : %s', data["return_code"], data.get("reason", "?"))
            elif msg.topic.startswith(MQTT_EVENT_TOPIC):
                programs = (data.get("precond_state") or {}).get("programs")
                if programs:
                    self.precond_programs[data["vin"]] = programs
                self._store_lock_state(data)
                self._dispatch_event(data)
        except KeyError:
            logger.exception("on_mqtt_message:")

    def _dispatch_event(self, data: dict) -> None:
        for callback in self.on_event:
            try:
                callback(data)
            except Exception:
                logger.exception("mqtt event callback failed")

    def _store_lock_state(self, data):
        # doors_state can be absent or explicitly null depending on the event
        locking_state = (data.get("doors_state") or {}).get("doors_locking_state")
        if locking_state is None:
            return
        lock_state = self._decode_lock_state(locking_state)
        if lock_state is None:
            return
        vin = data["vin"]
        self.lock_state[vin] = lock_state
        self.lock_state_at[vin] = parse_event_date(data.get("date"))

    @staticmethod
    def _decode_lock_state(locking_state):
        lock_state = MQTT_DOORS_LOCKING_STATE.get(locking_state)
        if lock_state is None:
            logger.warning("unknown doors_locking_state %s, please report it on github", locking_state)
        return lock_state

    def start(self):
        if self.load_otp():
            self.mqtt_client = mqtt.Client(clean_session=True, protocol=mqtt.MQTTv311)
            if environ.get("MQTT_LOG", "0") == "1":
                self.mqtt_client.enable_logger(logger=logger)
            if self._refresh_remote_token():
                self.mqtt_client.tls_set_context()
                self.mqtt_client.on_connect = self.__on_mqtt_connect
                self.mqtt_client.on_message = self._on_mqtt_message
                self.mqtt_client.on_disconnect = self._on_mqtt_disconnect
                self.mqtt_client.connect(MQTT_SERVER, 8885, 60)
                self.mqtt_client.loop_start()
                self.__keep_mqtt()
                return self.mqtt_client.is_connected()
        logger.error("Can't configure MQTT Client")
        return False

    def stop(self):
        if self.mqtt_client:
            logger.info("stop mqtt...")
            self.mqtt_client.on_disconnect = None
            self.mqtt_client.disconnect()
        if self.update_thread:
            self.update_thread.cancel()
            self.update_thread.join(timeout=TIMEOUT_IN_S)

    def __keep_mqtt(self):  # avoid token expiration
        if len(self.vehicles_list) > 0:
            try:
                self.wakeup(self.vehicles_list[0].vin)
            except Exception:
                logger.exception("__keep_mqtt")
        self.update_thread = threading.Timer(KEEPALIVE_PERIOD, self.__keep_mqtt)
        self.update_thread.daemon = True
        self.update_thread.start()

    def veh_charge_request(self, vin, hour, minute, charge_type):
        msg = self.mqtt_request(vin, {"program": {"hour": hour, "minute": minute}, "type": charge_type}, "/VehCharge")
        logger.info("veh_charge_request: %s", msg)
        return self.publish(msg)

    def publish(self, mqtt_request: MQTTRequest, store=True) -> bool:
        if not self._refresh_remote_token():
            logger.error("Can't publish %s: remote token refresh failed", mqtt_request.topic)
            return False
        message = mqtt_request.get_message_to_json(self.remoteCredentials.access_token)
        logger.debug("mqtt publish: %s %s", mqtt_request.topic, message)
        self.mqtt_client.publish(mqtt_request.topic, message)
        if store:
            self.last_request = mqtt_request
        return True

    def mqtt_request(self, vin, req_parameters, topic):
        return MQTTRequest(topic, vin, req_parameters, self.account_info.get_mqtt_customer_id())

    def _refresh_remote_token(self, force=False) -> bool:
        with self._lock:
            bad_remote_token = self.remoteCredentials.refresh_token is None
            if not force and not bad_remote_token and self.remoteCredentials.last_update:
                last_update: datetime = self.remoteCredentials.last_update
                if (datetime.now() - last_update).total_seconds() < MQTT_TOKEN_TTL:
                    return True
            if force and self.remote_token_last_update is not None:
                # throttle forced refreshes (e.g. one per paho reconnect attempt on rc 7):
                # a stale token makes the broker drop the socket, and paho retries at most every
                # MAX_REFRESH_DELAY seconds, so refreshing more often than that is either
                # useless (network outage: the token isn't the problem) or harmful
                # (it burns the refresh_token_now() rate limit: 6 per 30 min).
                since_last_refresh = (datetime.now() - self.remote_token_last_update).total_seconds()
                if since_last_refresh < MAX_REFRESH_DELAY:
                    logger.debug("remote token refreshed %.0fs ago, skipping forced refresh",
                                 since_last_refresh)
                    return True
            try:
                if not self.manager.refresh_token_now():
                    logger.error("Can't refresh remote token: access token refresh failed")
                    return False
                if bad_remote_token:
                    logger.warning("remote_refresh_token isn't defined")
                else:
                    res = self.manager.post(REMOTE_URL + self.account_info.client_id,
                                            json={"grant_type": "refresh_token",
                                                  "refresh_token": self.remoteCredentials.refresh_token},
                                            headers=self.headers, timeout=TIMEOUT_IN_S)
                    data = res.json()
                    logger.debug("refresh_remote_token: %s", data)
                    if "access_token" in data:
                        self.remoteCredentials.access_token = data["access_token"]
                        bad_remote_token = False
                        if "refresh_token" in data:
                            self.remoteCredentials.refresh_token = data["refresh_token"]
                    else:
                        logger.error("can't refresh_remote_token: %s\n Create a new one", data)
                        bad_remote_token = True
                if bad_remote_token:
                    otp_code = self.get_otp_code()
                    self._get_remote_access_token(otp_code)
                self.remote_token_last_update = datetime.now()
                self.mqtt_client.username_pw_set("IMA_OAUTH_ACCESS_TOKEN", self.remoteCredentials.access_token)
                self.auth_failed = False
                return True
            except RateLimitException as e:
                logger.error("Can't refresh remote token please wait... %s", e)
                return False
            except (RemoteException, ConfigException):
                logger.exception("Can't refresh remote token, please redo otp procedure")
                self.auth_failed = True
                return False
            except (RequestException, KeyError, AttributeError):
                logger.exception("Can't refresh remote token, please redo otp procedure")
                return False

    def get_sms_otp_code(self):
        res = self.manager.post(
            "https://api.groupe-psa.com/applications/cvs/v4/mobile/smsCode?client_id=" + self.account_info.client_id,
            headers=self.headers, timeout=TIMEOUT_IN_S)
        logger.info("SMS code request: HTTP %s", res.status_code)
        if not res.ok:
            logger.error("SMS code request failed: HTTP %s %s", res.status_code, res.text)
            raise RemoteException(f"SMS request failed: HTTP {res.status_code} {res.text}")
        return res

    # 6 otp by day
    @rate_limit(6, 3600 * 24)
    def get_otp_code(self):
        try:
            otp_code = self.otp.get_otp_code()
        except ConfigException:
            logger.exception("get_otp_code:")
            # load_otp() re-reads otp.bin from disk: it is the ONLY recovery when the pickled
            # session is desynchronized with the server. force_new would NOT reload it (it
            # always takes the "Please redo otp config" branch and returns False), so the old
            # code retried the very session that just failed.
            if not self.load_otp():
                raise
            otp_code = self.otp.get_otp_code()
        save_otp(self.otp)
        return otp_code

    def _get_remote_access_token(self, password):
        res = self.manager.post(REMOTE_URL + self.account_info.client_id,
                                json={"grant_type": "password", "password": password},
                                headers=self.headers, timeout=TIMEOUT_IN_S)
        data = res.json()
        try:
            self.remoteCredentials.access_token = data["access_token"]
            self.remoteCredentials.refresh_token = data["refresh_token"]
        except KeyError as e:
            raise RemoteException("get_remote_access_token: bad response" + str(data)) from e
        return res

    def horn(self, vin, count):
        msg = self.mqtt_request(vin, {"nb_horn": count, "action": "activate"}, "/Horn")
        logger.info(msg)
        return self.publish(msg)

    def lights(self, vin, duration: int):
        msg = self.mqtt_request(vin, {"action": "activate", "duration": duration}, "/Lights")
        logger.info(msg)
        return self.publish(msg)

    @rate_limit(6, 60 * 20)
    def wakeup(self, vin):
        logger.info("ask wakeup to %s", vin)
        msg = self.mqtt_request(vin, {"action": "state"}, "/VehCharge/state")
        logger.info(msg)
        return self.publish(msg)

    def lock_door(self, vin, lock: bool):
        if lock:
            value = "lock"
        else:
            value = "unlock"

        msg = self.mqtt_request(vin, {"action": value}, "/Doors")
        logger.info(msg)
        return self.publish(msg)

    def preconditioning(self, vin, activate: bool):
        if activate:
            value = "activate"
        else:
            value = "deactivate"
        if vin in self.precond_programs:
            programs = self.precond_programs[vin]
        else:
            programs = DEFAULT_PRECONDITIONING_PROGRAM
        msg = self.mqtt_request(vin, {"asap": value, "programs": programs}, "/ThermalPrecond")
        logger.info("Preconditioning: %s", msg)
        return self.publish(msg)

    def load_otp(self, force_new=False):
        otp_session = load_otp()
        if otp_session is None or force_new:
            logger.error("Please redo otp config")
            return False
        self.otp = otp_session
        return True

    def change_charge_hour(self, vin, hour, minute):
        return self.veh_charge_request(vin, hour, minute, DELAYED_CHARGE)
