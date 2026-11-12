import bz2
import logging
from hashlib import sha1
from pathlib import Path

import requests

logger = logging.getLogger(__name__)
TIMEOUT_IN_S = 20


def get_github_sha_from_file(user, repo, directory, filename):
    res = requests.get("https://api.github.com/repos/{}/{}/git/trees/main:{}".format(user, repo, directory),
                       timeout=TIMEOUT_IN_S).json()
    try:
        file_info = next((file for file in res["tree"] if file['path'] == filename))
    except KeyError as e:
        logger.error("can't get github file: %s", res)
        raise e
    return file_info["sha"]


def github_file_need_to_be_downloaded(user, repo, directory, filename, local_path: Path):
    try:
        with open(local_path, 'rb') as file_for_hash:
            data = file_for_hash.read()
            filesize = len(data)
        prefix = "blob " + str(filesize) + "\0"
        sha_of_downloaded_file = sha1(prefix.encode("utf-8") + data).hexdigest()
        sha_of_git_file = get_github_sha_from_file(user, repo, directory, filename)
        if sha_of_downloaded_file == sha_of_git_file:
            logger.debug("locale file is the latest version")
            return False
        logger.debug("download last version of file")
    except FileNotFoundError:
        logger.debug("File not found, download file")
    return True


def urlretrieve_from_github(user, repo, directory, filename, dest_dir: Path, branch="main") -> Path:
    """Download `filename`.bz2 from the repo into dest_dir (if changed) and decompress it; return the local path."""
    archive_name = filename + ".bz2"
    archive_path, local_path = dest_dir / archive_name, dest_dir / filename
    stale = github_file_need_to_be_downloaded(user, repo, directory, archive_name, archive_path)
    if stale or not local_path.is_file():
        with open(archive_path, 'wb') as f:
            url = "https://github.com/{}/{}/raw/{}/{}{}".format(user, repo, branch, directory, archive_name)
            r = requests.get(url,
                             headers={
                                 "Accept": "application/vnd.github.VERSION.raw"
                             },
                             stream=True,
                             timeout=TIMEOUT_IN_S
                             )

            r.raise_for_status()
            for chunk in r.iter_content(1024):
                f.write(chunk)
        with bz2.BZ2File(archive_path, 'rb') as file, open(local_path, 'wb') as out_file:
            out_file.write(file.read())
    return local_path
