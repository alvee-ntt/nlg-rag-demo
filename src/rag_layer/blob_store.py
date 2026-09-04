from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Iterable
from urllib.parse import quote
from xml.etree import ElementTree as ET

import requests
import urllib3

from .config import Settings


@dataclass(frozen=True)
class BlobInfo:
    name: str
    etag: str | None = None
    last_modified: object | None = None
    size: int | None = None


class SasBlobStore:
    def __init__(self, settings: Settings) -> None:
        if not settings.azure_storage_account or not settings.azure_storage_container:
            raise ValueError("AZURE_STORAGE_ACCOUNT and AZURE_STORAGE_CONTAINER are required")
        if not settings.azure_storage_sas_token:
            raise ValueError("AZURE_STORAGE_SAS_TOKEN is required for SAS blob access")
        self.account = settings.azure_storage_account
        self.container = settings.azure_storage_container
        self.sas = settings.azure_storage_sas_token.lstrip("?")
        self.base_url = f"https://{self.account}.blob.core.windows.net/{self.container}"
        self.verify_ssl = settings.azure_storage_verify_ssl
        self.session = requests.Session()
        self.session.trust_env = False
        if not self.verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def iter_blobs(self, prefixes: list[str]) -> Iterable[BlobInfo]:
        active_prefixes = prefixes or [""]
        for prefix in active_prefixes:
            marker = ""
            while True:
                params = f"restype=container&comp=list&maxresults=250&{self.sas}"
                if prefix:
                    params += f"&prefix={quote(prefix.strip('/') + '/', safe='/')}"
                if marker:
                    params += f"&marker={quote(marker)}"
                response = self.session.get(f"{self.base_url}?{params}", timeout=(10, 30), verify=self.verify_ssl)
                response.raise_for_status()
                root = ET.fromstring(response.content)
                for blob in root.findall("./Blobs/Blob"):
                    name = blob.findtext("Name") or ""
                    props = blob.find("Properties")
                    last_modified = props.findtext("Last-Modified") if props is not None else None
                    size_text = props.findtext("Content-Length") if props is not None else None
                    yield BlobInfo(
                        name=name,
                        etag=props.findtext("Etag") if props is not None else None,
                        last_modified=parsedate_to_datetime(last_modified) if last_modified else None,
                        size=int(size_text) if size_text else None,
                    )
                marker = root.findtext("NextMarker") or ""
                if not marker:
                    break

    def download_blob(self, blob_name: str) -> bytes:
        encoded_name = quote(blob_name, safe="/")
        response = self.session.get(f"{self.base_url}/{encoded_name}?{self.sas}", timeout=(10, 120), verify=self.verify_ssl)
        response.raise_for_status()
        return response.content


def get_blob_store(settings: Settings) -> SasBlobStore:
    if settings.azure_storage_connection_string:
        raise NotImplementedError("This project currently uses AZURE_STORAGE_SAS_TOKEN for blob access")
    return SasBlobStore(settings)


