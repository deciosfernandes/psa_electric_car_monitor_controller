import logging

from oauth2_client.credentials_manager import ServiceInformation
from urllib3.exceptions import HTTPError

from psacc.car import Cars, Car
from psacc.psa.AccountInformation import AccountInformation
from psacc.psa.RemoteClient import RemoteClient
from psacc.psa.RemoteCredentials import RemoteCredentials
from psacc.psa.connected_car_api.api.vehicles_api import VehiclesApi
from psacc.psa.connected_car_api.rest import ApiException
from psacc.psa.constants import realm_info, AUTHORIZE_SERVICE
from psacc.psa.oauth import OpenIdCredentialManager, Oauth2PSACCApiConfig, OauthAPIClient

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
        self.api_config = Oauth2PSACCApiConfig()
        self.api_config.set_refresh_callback(self.manager.refresh_token_now)
        self.manager.refresh_token = refresh_token
        self.account_info = AccountInformation(client_id, customer_id, realm, country_code)
        self.vehicles_list = Cars()
        self.customer_id = customer_id
        self.api_config.verify_ssl = True
        self.api_config.api_key['client_id'] = self.client_id
        self.api_config.api_key['x-introspect-realm'] = self.realm
        self.brand = brand
        self.api_config.proxy = None
        self.manager.proxies = {"http": '', "https": ''}
        self.remote_client = RemoteClient(self.account_info,
                                          self.vehicles_list,
                                          self.manager,
                                          RemoteCredentials(remote_refresh_token))

    def get_app_name(self):
        return realm_info[self.realm]['app_name']

    def api(self) -> VehiclesApi:
        self.api_config.access_token = self.manager.access_token
        return VehiclesApi(OauthAPIClient(self.api_config))

    def get_vehicle_info(self, vin, cache=False):
        res = None
        car = self.vehicles_list.get_car_by_vin(vin)
        if cache and car.status is not None:
            res = car.status
        else:
            for _ in range(0, 2):
                try:
                    res = self.api().get_vehicle_status(car.vehicle_id)
                    if res is not None:
                        car.status = res
                        # a new status wipes anything mqtt filled in, so put it back
                        self.remote_client.apply_lock_state(car)
                        return res
                except (ApiException, HTTPError) as ex:
                    logger.error("get_vehicle_info: ApiException: %s", ex, exc_info=logger.isEnabledFor(logging.DEBUG))
            car.status = res
        return res

    def get_vehicles(self):
        try:
            res = self.api().get_vehicles_by_device()
            for vehicle in res.embedded.vehicles:
                self.vehicles_list.add(Car(vehicle.vin, vehicle.id, vehicle.brand, vehicle.label))
        except (ApiException, HTTPError):
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
