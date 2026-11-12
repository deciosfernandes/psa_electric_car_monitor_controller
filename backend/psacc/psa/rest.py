import logging

import requests

from psacc.psa.models import VehicleRef, VehicleStatus
from psacc.psa.oauth import OpenIdCredentialManager
from psacc.utils import RateLimitException

logger = logging.getLogger(__name__)

BASE_URL = "https://api.groupe-psa.com/connectedcar/v4"
TIMEOUT_S = 20


class PsaRest:
    """Read-only client for the official PSA connected car REST API."""

    def __init__(self, manager: OpenIdCredentialManager, client_id: str, realm: str, base_url: str = BASE_URL,
                 session: requests.Session | None = None):
        self.manager = manager
        self.client_id = client_id
        self.realm = realm
        self.base_url = base_url
        self.session = session or requests.Session()

    def _request(self, path: str, params: dict) -> requests.Response:
        return self.session.get(self.base_url + path, params={"client_id": self.client_id, **params},
                                headers={"x-introspect-realm": self.realm,
                                         "Authorization": "Bearer " + str(self.manager.access_token),
                                         "Accept": "application/hal+json"},
                                timeout=TIMEOUT_S)

    def get(self, path: str, **params) -> dict:
        response = self._request(path, params)
        if response.status_code == requests.codes.unauthorized:
            try:
                refreshed = self.manager.refresh_token_now()
            except RateLimitException:
                logger.warning("token refresh rate limited")
                refreshed = False
            if refreshed:
                response = self._request(path, params)
        response.raise_for_status()
        return response.json()

    def vehicles(self) -> list[VehicleRef]:
        data = self.get("/user/vehicles")
        return [VehicleRef.model_validate(vehicle) for vehicle in data.get("_embedded", {}).get("vehicles", [])]

    def status(self, vehicle_id: str) -> VehicleStatus:
        return VehicleStatus.model_validate(self.get(f"/user/vehicles/{vehicle_id}/status"))
