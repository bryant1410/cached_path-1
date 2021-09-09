from collections import Counter
import json
import os
import shutil
import time
import pathlib

from filelock import Timeout
import pytest
import responses
from requests.exceptions import ConnectionError, HTTPError

import cached_path as file_utils
from cached_path import (
    FileLock,
    _resource_to_filename,
    filename_to_url,
    get_from_cache,
    get_cached_path,
    _split_s3_path,
    _split_gcs_path,
    CacheFile,
    _Meta,
)

from cached_path.testing import BaseTestClass


def set_up_glove(url: str, byt: bytes, change_etag_every: int = 1000):
    # Mock response for the datastore url that returns glove vectors
    responses.add(
        responses.GET,
        url,
        body=byt,
        status=200,
        content_type="application/gzip",
        stream=True,
        headers={"Content-Length": str(len(byt))},
    )

    etags_left = change_etag_every
    etag = "0"

    def head_callback(_):
        """
        Writing this as a callback allows different responses to different HEAD requests.
        In our case, we're going to change the ETag header every `change_etag_every`
        requests, which will allow us to simulate having a new version of the file.
        """
        nonlocal etags_left, etag
        headers = {"ETag": etag}
        # countdown and change ETag
        etags_left -= 1
        if etags_left <= 0:
            etags_left = change_etag_every
            etag = str(int(etag) + 1)
        return (200, headers, "")

    responses.add_callback(responses.HEAD, url, callback=head_callback)


class TestFileLock(BaseTestClass):
    def setup_method(self):
        super().setup_method()

        # Set up a regular lock and a read-only lock.
        open(self.TEST_DIR / "lock", "a").close()
        open(self.TEST_DIR / "read_only_lock", "a").close()
        os.chmod(self.TEST_DIR / "read_only_lock", 0o555)

        # Also set up a read-only directory.
        os.mkdir(self.TEST_DIR / "read_only_dir", 0o555)

    def test_locking(self):
        with FileLock(self.TEST_DIR / "lock"):
            # Trying to acquire the lock again should fail.
            with pytest.raises(Timeout):
                with FileLock(self.TEST_DIR / "lock", timeout=0.1):
                    pass

        # Trying to acquire a lock when lacking write permissions on the file should fail.
        with pytest.raises(PermissionError):
            with FileLock(self.TEST_DIR / "read_only_lock"):
                pass

        # But this should only issue a warning if we set the `read_only_ok` flag to `True`.
        with pytest.warns(UserWarning, match="Lacking permissions"):
            with FileLock(self.TEST_DIR / "read_only_lock", read_only_ok=True):
                pass

        # However this should always fail when we lack write permissions and the file lock
        # doesn't exist yet.
        with pytest.raises(PermissionError):
            with FileLock(self.TEST_DIR / "read_only_dir" / "lock", read_only_ok=True):
                pass


class TestCacheFile(BaseTestClass):
    def test_temp_file_removed_on_error(self):
        cache_filename = self.TEST_DIR / "cache_file"
        with pytest.raises(IOError, match="I made this up"):
            with CacheFile(cache_filename) as handle:
                raise IOError("I made this up")
        assert not os.path.exists(handle.name)
        assert not os.path.exists(cache_filename)


class TestFileUtils(BaseTestClass):
    def setup_method(self):
        super().setup_method()
        self.glove_file = self.FIXTURES_ROOT / "embeddings/glove.6B.100d.sample.txt.gz"
        with open(self.glove_file, "rb") as glove:
            self.glove_bytes = glove.read()

    def test_cached_path_offline(self, monkeypatch):
        # Ensures `cached_path` just returns the path to the latest cached version
        # of the resource when there's no internet connection.

        # First we mock the `_http_etag` method so that it raises a `ConnectionError`,
        # like it would if there was no internet connection.
        def mocked_http_etag(url: str):
            raise ConnectionError

        monkeypatch.setattr(file_utils, "_http_etag", mocked_http_etag)

        url = "https://github.com/allenai/allennlp/blob/master/some-fake-resource"

        # We'll create two cached versions of this fake resource using two different etags.
        etags = ['W/"3e5885bfcbf4c47bc4ee9e2f6e5ea916"', 'W/"3e5885bfcbf4c47bc4ee9e2f6e5ea918"']
        filenames = [
            os.path.join(self.TEST_DIR, _resource_to_filename(url, etag)) for etag in etags
        ]
        for filename, etag in zip(filenames, etags):
            meta = _Meta(
                resource=url, cached_path=filename, creation_time=time.time(), etag=etag, size=2341
            )
            meta.to_file()
            with open(filename, "w") as f:
                f.write("some random data")
            # os.path.getmtime is only accurate to the second.
            time.sleep(1.1)

        # Should know to ignore lock files and extraction directories.
        with open(filenames[-1] + ".lock", "w") as f:
            f.write("")
        os.mkdir(filenames[-1] + "-extracted")

        # The version corresponding to the last etag should be returned, since
        # that one has the latest "last modified" time.
        assert get_from_cache(url, cache_dir=self.TEST_DIR) == filenames[-1]

        # We also want to make sure this works when the latest cached version doesn't
        # have a corresponding etag.
        filename = os.path.join(self.TEST_DIR, _resource_to_filename(url))
        meta = _Meta(resource=url, cached_path=filename, creation_time=time.time(), size=2341)
        with open(filename, "w") as f:
            f.write("some random data")

        assert get_from_cache(url, cache_dir=self.TEST_DIR) == filename

    def test_resource_to_filename(self):
        for url in [
            "http://allenai.org",
            "http://allennlp.org",
            "https://www.google.com",
            "http://pytorch.org",
            "https://allennlp.s3.amazonaws.com" + "/long" * 20 + "/url",
        ]:
            filename = _resource_to_filename(url)
            assert "http" not in filename
            with pytest.raises(FileNotFoundError):
                filename_to_url(filename, cache_dir=self.TEST_DIR)
            pathlib.Path(os.path.join(self.TEST_DIR, filename)).touch()
            with pytest.raises(FileNotFoundError):
                filename_to_url(filename, cache_dir=self.TEST_DIR)
            json.dump(
                {"url": url, "etag": None},
                open(os.path.join(self.TEST_DIR, filename + ".json"), "w"),
            )
            back_to_url, etag = filename_to_url(filename, cache_dir=self.TEST_DIR)
            assert back_to_url == url
            assert etag is None

    def test_resource_to_filename_with_etags(self):
        for url in [
            "http://allenai.org",
            "http://allennlp.org",
            "https://www.google.com",
            "http://pytorch.org",
        ]:
            filename = _resource_to_filename(url, etag="mytag")
            assert "http" not in filename
            pathlib.Path(os.path.join(self.TEST_DIR, filename)).touch()
            json.dump(
                {"url": url, "etag": "mytag"},
                open(os.path.join(self.TEST_DIR, filename + ".json"), "w"),
            )
            back_to_url, etag = filename_to_url(filename, cache_dir=self.TEST_DIR)
            assert back_to_url == url
            assert etag == "mytag"
        baseurl = "http://allenai.org/"
        assert _resource_to_filename(baseurl + "1") != _resource_to_filename(baseurl, etag="1")

    def test_resource_to_filename_with_etags_eliminates_quotes(self):
        for url in [
            "http://allenai.org",
            "http://allennlp.org",
            "https://www.google.com",
            "http://pytorch.org",
        ]:
            filename = _resource_to_filename(url, etag='"mytag"')
            assert "http" not in filename
            pathlib.Path(os.path.join(self.TEST_DIR, filename)).touch()
            json.dump(
                {"url": url, "etag": "mytag"},
                open(os.path.join(self.TEST_DIR, filename + ".json"), "w"),
            )
            back_to_url, etag = filename_to_url(filename, cache_dir=self.TEST_DIR)
            assert back_to_url == url
            assert etag == "mytag"

    def test_split_s3_path(self):
        # Test splitting good urls.
        assert _split_s3_path("s3://my-bucket/subdir/file.txt") == ("my-bucket", "subdir/file.txt")
        assert _split_s3_path("s3://my-bucket/file.txt") == ("my-bucket", "file.txt")

        # Test splitting bad urls.
        with pytest.raises(ValueError):
            _split_s3_path("s3://")
            _split_s3_path("s3://myfile.txt")
            _split_s3_path("myfile.txt")

    def test_split_gcs_path(self):
        # Test splitting good urls.
        assert _split_gcs_path("gs://my-bucket/subdir/file.txt") == ("my-bucket", "subdir/file.txt")
        assert _split_gcs_path("gs://my-bucket/file.txt") == ("my-bucket", "file.txt")

        # Test splitting bad urls.
        with pytest.raises(ValueError):
            _split_gcs_path("gs://")
            _split_gcs_path("gs://myfile.txt")
            _split_gcs_path("myfile.txt")

    @responses.activate
    def test_get_from_cache(self):
        url = "http://fake.datastore.com/glove.txt.gz"
        set_up_glove(url, self.glove_bytes, change_etag_every=2)

        filename = get_from_cache(url, cache_dir=self.TEST_DIR)
        assert filename == os.path.join(self.TEST_DIR, _resource_to_filename(url, etag="0"))
        assert os.path.exists(filename + ".json")
        meta = _Meta.from_path(filename + ".json")
        assert meta.resource == url

        # We should have made one HEAD request and one GET request.
        method_counts = Counter(call.request.method for call in responses.calls)
        assert len(method_counts) == 2
        assert method_counts["HEAD"] == 1
        assert method_counts["GET"] == 1

        # And the cached file should have the correct contents
        with open(filename, "rb") as cached_file:
            assert cached_file.read() == self.glove_bytes

        # A second call to `get_from_cache` should make another HEAD call
        # but not another GET call.
        filename2 = get_from_cache(url, cache_dir=self.TEST_DIR)
        assert filename2 == filename

        method_counts = Counter(call.request.method for call in responses.calls)
        assert len(method_counts) == 2
        assert method_counts["HEAD"] == 2
        assert method_counts["GET"] == 1

        with open(filename2, "rb") as cached_file:
            assert cached_file.read() == self.glove_bytes

        # A third call should have a different ETag and should force a new download,
        # which means another HEAD call and another GET call.
        filename3 = get_from_cache(url, cache_dir=self.TEST_DIR)
        assert filename3 == os.path.join(self.TEST_DIR, _resource_to_filename(url, etag="1"))

        method_counts = Counter(call.request.method for call in responses.calls)
        assert len(method_counts) == 2
        assert method_counts["HEAD"] == 3
        assert method_counts["GET"] == 2

        with open(filename3, "rb") as cached_file:
            assert cached_file.read() == self.glove_bytes

    @responses.activate
    def test_cached_path(self):
        url = "http://fake.datastore.com/glove.txt.gz"
        set_up_glove(url, self.glove_bytes)

        # non-existent file
        with pytest.raises(FileNotFoundError):
            filename = get_cached_path(self.FIXTURES_ROOT / "does_not_exist" / "fake_file.tar.gz")

        # unparsable URI
        with pytest.raises(ValueError):
            filename = get_cached_path("fakescheme://path/to/fake/file.tar.gz")

        # existing file as path
        assert get_cached_path(self.glove_file) == str(self.glove_file)

        # caches urls
        filename = get_cached_path(url, cache_dir=self.TEST_DIR)

        assert len(responses.calls) == 2
        assert filename == os.path.join(self.TEST_DIR, _resource_to_filename(url, etag="0"))

        with open(filename, "rb") as cached_file:
            assert cached_file.read() == self.glove_bytes

        # archives
        filename = get_cached_path(
            self.FIXTURES_ROOT / "common" / "quote.tar.gz!quote.txt",
            extract_archive=True,
            cache_dir=self.TEST_DIR,
        )
        with open(filename, "r") as f:
            assert f.read().startswith("I mean, ")

    @responses.activate
    def test_cached_path_http_err_handling(self):
        url_404 = "http://fake.datastore.com/does-not-exist"
        byt = b"Does not exist"
        for method in (responses.GET, responses.HEAD):
            responses.add(
                method,
                url_404,
                body=byt,
                status=404,
                headers={"Content-Length": str(len(byt))},
            )

        with pytest.raises(HTTPError):
            get_cached_path(url_404, cache_dir=self.TEST_DIR)

    def test_extract_with_external_symlink(self):
        dangerous_file = self.FIXTURES_ROOT / "common" / "external_symlink.tar.gz"
        with pytest.raises(ValueError):
            get_cached_path(dangerous_file, extract_archive=True)


class TestCachedPathWithArchive(BaseTestClass):
    def setup_method(self):
        super().setup_method()
        self.tar_file = self.TEST_DIR / "utf-8.tar.gz"
        shutil.copyfile(
            self.FIXTURES_ROOT / "utf-8_sample" / "archives" / "utf-8.tar.gz", self.tar_file
        )
        self.zip_file = self.TEST_DIR / "utf-8.zip"
        shutil.copyfile(
            self.FIXTURES_ROOT / "utf-8_sample" / "archives" / "utf-8.zip", self.zip_file
        )

    def check_extracted(self, extracted: str):
        assert os.path.isdir(extracted)
        assert pathlib.Path(extracted).parent == self.TEST_DIR
        assert os.path.exists(os.path.join(extracted, "dummy.txt"))
        assert os.path.exists(os.path.join(extracted, "folder/utf-8_sample.txt"))
        assert os.path.exists(extracted + ".json")

    def test_cached_path_extract_local_tar(self):
        extracted = get_cached_path(self.tar_file, cache_dir=self.TEST_DIR, extract_archive=True)
        self.check_extracted(extracted)

    def test_cached_path_extract_local_zip(self):
        extracted = get_cached_path(self.zip_file, cache_dir=self.TEST_DIR, extract_archive=True)
        self.check_extracted(extracted)

    @responses.activate
    def test_cached_path_extract_remote_tar(self):
        url = "http://fake.datastore.com/utf-8.tar.gz"
        byt = open(self.tar_file, "rb").read()

        responses.add(
            responses.GET,
            url,
            body=byt,
            status=200,
            content_type="application/tar+gzip",
            stream=True,
            headers={"Content-Length": str(len(byt))},
        )
        responses.add(
            responses.HEAD,
            url,
            status=200,
            headers={"ETag": "fake-etag"},
        )

        extracted = get_cached_path(url, cache_dir=self.TEST_DIR, extract_archive=True)
        assert extracted.endswith("-extracted")
        self.check_extracted(extracted)

    @responses.activate
    def test_cached_path_extract_remote_zip(self):
        url = "http://fake.datastore.com/utf-8.zip"
        byt = open(self.zip_file, "rb").read()

        responses.add(
            responses.GET,
            url,
            body=byt,
            status=200,
            content_type="application/zip",
            stream=True,
            headers={"Content-Length": str(len(byt))},
        )
        responses.add(
            responses.HEAD,
            url,
            status=200,
            headers={"ETag": "fake-etag"},
        )

        extracted = get_cached_path(url, cache_dir=self.TEST_DIR, extract_archive=True)
        assert extracted.endswith("-extracted")
        self.check_extracted(extracted)


class TestHFHubDownload(BaseTestClass):
    def test_cached_download_no_user_or_org(self):
        path = get_cached_path("hf://t5-small/config.json", cache_dir=self.TEST_DIR)
        assert os.path.isfile(path)
        assert pathlib.Path(os.path.dirname(path)) == self.TEST_DIR
        assert os.path.isfile(path + ".json")
        meta = _Meta.from_path(path + ".json")
        assert meta.etag is not None
        assert meta.resource == "hf://t5-small/config.json"

    def test_snapshot_download_no_user_or_org(self):
        # This is the smallest snapshot I could find that is not associated with a user / org.
        model_name = "distilbert-base-german-cased"
        path = get_cached_path(f"hf://{model_name}")
        assert os.path.isdir(path)
        assert os.path.isfile(path + ".json")
        meta = _Meta.from_path(path + ".json")
        assert meta.resource == f"hf://{model_name}"