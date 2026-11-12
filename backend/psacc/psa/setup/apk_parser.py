import json
import os
import sys

from androguard.core.apk import APK
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

from psacc.psa.constants import BRAND
from psacc.psa.setup.paths import cert_paths

from androguard.core.axml import logger as androguard_logger
androguard_logger.remove()
androguard_logger.add(sys.stderr, level="ERROR")


class ApkParser:
    def __init__(self, filename, country_code):
        self.country_code = country_code
        self.filename = filename
        self.host_brandid_prod = None
        self.site_code = None
        self.culture = None
        self.client_id = None
        self.client_secret = None
        self.version = None

    @staticmethod
    def __get_cultures_code(file, country_code):
        cultures = json.loads(file)
        return cultures[country_code]["languages"][0]

    def __get_parameters_path(self):
        language, country = self.culture.split("_")
        return "res/raw-{}-r{}/parameters.json".format(language, country)

    def retrieve_content_from_apk(self):
        a = APK(self.filename)
        package_name = a.get_package()
        resources = a.get_android_resources()
        self.host_brandid_prod = resources.get_string(package_name, "HOST_BRANDID_PROD")[1]
        self.culture = self.__get_cultures_code(a.get_file("res/raw/cultures.json"), self.country_code)
        parameters = json.loads(a.get_file(self.__get_parameters_path()))
        self.client_id = parameters["cvsClientId"]
        self.client_secret = parameters["cvsSecret"]

        # Get Customer id
        self.site_code = BRAND[package_name]["brand_code"] + "_" + self.country_code + "_ESP"
        self.version = a.androidversion.get("Name")
        pfx_cert = a.get_file("assets/MWPMYMA1.pfx")
        save_key_to_pem(pfx_cert, b"y5Y2my5B")


def _write_private(path: str, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    if os.name == "posix":
        os.chmod(path, 0o600)


def save_key_to_pem(pfx_data, pfx_password):
    private_key, certificate = pkcs12.load_key_and_certificates(pfx_data,
                                                                pfx_password, default_backend())[:2]
    public_path, private_path = cert_paths()
    _write_private(public_path, certificate.public_bytes(encoding=serialization.Encoding.PEM))
    _write_private(private_path, private_key.private_bytes(encoding=serialization.Encoding.PEM,
                                                           format=serialization.PrivateFormat.TraditionalOpenSSL,
                                                           encryption_algorithm=serialization.NoEncryption()))
