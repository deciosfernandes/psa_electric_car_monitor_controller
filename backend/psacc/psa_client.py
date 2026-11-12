import logging

from oauth2_client.credentials_manager import ServiceInformation
from requests import RequestException

from psacc.car import Cars, Car
from psacc.psa.AccountInformation import AccountInformation
from psacc.psa.RemoteClient import RemoteClient
from psacc.psa.RemoteCredentials import RemoteCredentials
from psacc.psa.constants import realm_info, AUTHORIZE_SERVICE
from psacc.psa.models import VehicleStatus
from psacc.psa.oauth import OpenIdCredentialManager
from psacc.psa.rest import PsaRest

SCOPE = ['openid profile']

logger = logging.getLogger(__name__)


class PSAClient:
    def connect(self, code: str):
        self.manager.connect_with_code(code)

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def __init__(self, refresh_token, client_id, client_secret, remote_refresh_token, customer_id, realm, country_code,
                 brand=None):
        self.realm = realm
        self.service_information = ServiceInformation(AUTHORIZE_SERVICE[self.realm],
                                                      realm_info[self.realm]['oauth_url'],
                                                      client_id,
                                                      client_secret,
                                                      SCOPE, True)
        self.client_id = client_id
        self.country_code = country_code
        self.manager = OpenIdCredentialManager.create(self.service_information,
                                                      realm_info[self.realm]["scheme"], self.country_code)
        self.manager.refresh_token = refresh_token
        self.account_info = AccountInformation(client_id, customer_id, realm, country_code)
        self.vehicles_list = Cars()
        self.customer_id = customer_id
        self.rest = PsaRest(self.manager, client_id, realm)
        self.brand = brand
        self.manager.proxies = {"http": '', "https": ''}
        self.remote_client = RemoteClient(self.account_info,
                                          self.vehicles_list,
                                          self.manager,
                                          RemoteCredentials(remote_refresh_token))

        def _persist_tokens():
            try:
                from psacc import store
                store.save_psa(self.to_dict())
            except Exception:
                logger.debug("could not auto-persist psa tokens", exc_info=True)

        self.manager.refresh_callbacks.append(_persist_tokens)
        self.remote_client.remoteCredentials.update_callbacks.append(_persist_tokens)

    def get_app_name(self):
        return realm_info[self.realm]['app_name']

    def get_vehicle_info(self, vin, cache=False) -> VehicleStatus | None:
        car = self.vehicles_list.get_car_by_vin(vin)
        if cache and car.status is not None:
            return car.status
        try:
            car.status = self.rest.status(car.vehicle_id)
        except (RequestException, ValueError) as ex:
            logger.error("get_vehicle_info: %s", ex, exc_info=logger.isEnabledFor(logging.DEBUG))
            car.status = None
        return car.status

    def get_vehicles(self):
        try:
            for vehicle in self.rest.vehicles():
                self.vehicles_list.add(Car(vehicle.vin, vehicle.id, vehicle.brand, vehicle.label))
        except (RequestException, ValueError):
            logger.exception("get_vehicles:")
        return self.vehicles_list

    def to_dict(self) -> dict:
        return {"refresh_token": self.manager.refresh_token,
                "client_secret": self.service_information.client_secret,
                "remote_refresh_token": self.remote_client.remoteCredentials.refresh_token,
                "customer_id": self.account_info.customer_id,
                "client_id": self.account_info.client_id,
                "realm": self.account_info.realm,
                "country_code": self.account_info.country_code,
                "brand": self.brand,
                "vehicles": [car.to_dict() for car in self.vehicles_list]}

    @classmethod
    def from_dict(cls, data: dict) -> "PSAClient":
        data = dict(data)
        vehicles = data.pop("vehicles", [])
        client = cls(**data)
        client.vehicles_list.extend(Cars.from_json(vehicles))
        return client
