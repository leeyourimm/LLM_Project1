"""공시 원문 저장소. 원문을 모두 보관해서 재처리와 API 한도에 대응한다."""

from pathlib import Path
from typing import Protocol

from dartrag.config import Settings


class RawStore(Protocol):
    def exists(self, key: str) -> bool: ...
    def get(self, key: str) -> bytes: ...
    def put(self, key: str, data: bytes) -> None: ...


class LocalRawStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"잘못된 키: {key}")
        return path

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)


class S3RawStore:
    def __init__(self, bucket: str, endpoint_url: str | None = None):
        import boto3

        self.bucket = bucket
        self._s3 = boto3.client("s3", endpoint_url=endpoint_url)

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def get(self, key: str) -> bytes:
        return self._s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def put(self, key: str, data: bytes) -> None:
        self._s3.put_object(Bucket=self.bucket, Key=key, Body=data)


def make_raw_store(settings: Settings) -> RawStore:
    if settings.raw_store == "s3":
        return S3RawStore(settings.s3_bucket, settings.s3_endpoint_url)
    return LocalRawStore(settings.raw_store_dir)


def document_key(rcept_no: str) -> str:
    return f"documents/{rcept_no[:4]}/{rcept_no}.zip"
