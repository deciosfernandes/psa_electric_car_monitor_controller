#!/usr/bin/env python3
import json
import logging
import traceback

import requests

from psacc.psa.constants import BRAND
from psacc.psa.setup.apk_parser import ApkParser
from psacc.psa.setup.github import urlretrieve_from_github
from psacc.psa.setup.paths import apk_dir, cert_paths
from psacc.psa_client import PSAClient

logger = logging.getLogger(__name__)

GITHUB_USER = "flobz"
GITHUB_REPO = "psa_apk"
TIMEOUT_IN_S = 30


def get_content_from_apk(filename: str, country_code: str) -> ApkParser:
    local = urlretrieve_from_github(GITHUB_USER, GITHUB_REPO, "", filename, dest_dir=apk_dir())
    apk_parser = ApkParser(str(local), country_code)
    apk_parser.retrieve_content_from_apk()
    return apk_parser


class InitialSetup:
    def __init__(self, package_name, client_email, client_password, country_code):
        self.package_name = package_name
        filename = package_name.split(".")[-1] + ".apk"
        apk_parser = get_content_from_apk(filename, country_code)
        self.culture = apk_parser.culture
        self.site_code = apk_parser.site_code
        self.client_id = apk_parser.client_id
        self.client_secret = apk_parser.client_secret
        self.version = apk_parser.version
        self.country_code = country_code
        self.user_info = None
        self.customer_id = None
        try:
            res = requests.post(apk_parser.host_brandid_prod + "/GetAccessToken",
                                headers={
                                    "Connection": "Keep-Alive",
                                    "Content-Type": "application/json",
                                    "User-Agent": "okhttp/2.3.0"
                                },
                                params={"jsonRequest": json.dumps(
                                    {"siteCode": apk_parser.site_code, "culture": "fr-FR", "action": "authenticate",
                                     "fields": {"USR_EMAIL": {"value": client_email},
                                                "USR_PASSWORD": {"value": client_password}}
                                     }
                                )},
                                timeout=TIMEOUT_IN_S
                                )
            data = res.json()
            if token := data.get("accessToken"):
                self.token = token
            elif token := data.get("token"):
                self.token = token
            else:
                raise ConnectionError("No access token in response:", res.text)
        except ConnectionError as e:
            raise e
        except Exception as ex:
            msg = traceback.format_exc() + f"\nHOST_BRANDID : {apk_parser.host_brandid_prod} " \
                f"sitecode: {apk_parser.site_code}"
            try:
                msg += res.text
            except BaseException:
                pass
            logger.error(msg)
            raise ConnectionError(msg) from ex

        # Psacc
        self.user_info = self.__fetch_user_info()
        self.customer_id = BRAND[self.package_name]["brand_code"] + "-" + self.user_info["id"]

        self.psacc = PSAClient(None, self.client_id, self.client_secret,
                               None, self.customer_id, BRAND[self.package_name]["realm"],
                               self.country_code, BRAND[self.package_name]["brand_code"])

    def __fetch_user_info(self):
        try:
            res2 = requests.post(
                f"https://mw-{BRAND[self.package_name]['brand_code'].lower()}-m2c.mym.awsmpsa.com/api/v1/user",
                params={
                    "culture": self.culture,
                    "width": 1080,
                    "version": self.version
                },
                data=json.dumps({"site_code": self.site_code, "ticket": self.token}),
                headers={
                    "Connection": "Keep-Alive",
                    "Content-Type": "application/json;charset=UTF-8",
                    "Source-Agent": "App-Android",
                    "Token": self.token,
                    "User-Agent": "okhttp/4.8.0",
                    "Version": self.version
                },
                cert=cert_paths(),
                timeout=TIMEOUT_IN_S
            )

            res_dict = res2.json()["success"]
        except Exception as ex:
            msg = traceback.format_exc()
            try:
                msg += res2.text
            except BaseException:
                pass
            logger.error(msg)
            raise ConnectionError(msg) from ex
        return res_dict

    def connect(self, code) -> PSAClient:
        self.psacc.connect(code)
        res = self.psacc.get_vehicles()

        if len(res) == 0:
            raise ValueError(
                "No vehicle in your account is compatible with this API, you vehicle is probably too old...")

        for vehicle in self.user_info["vehicles"]:
            if vin := vehicle.get("vin"):
                car = self.psacc.vehicles_list.get_car_by_vin(vin)
                if car is not None and "short_label" in vehicle and car.label == "unknown":
                    car.label = vehicle["short_label"].split(" ")[-1]  # remove new, nouvelle, neu word....

        logger.info("Your vehicles: %s", res)
        return self.psacc
