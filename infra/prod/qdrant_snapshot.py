"""Qdrant 컬렉션 스냅샷 백업·복원 (backup.sh / restore.sh 가 api 이미지 안에서 실행).

    python qdrant_snapshot.py backup  <폴더>   # 컬렉션마다 <이름>.snapshot 저장
    python qdrant_snapshot.py restore <폴더>   # 폴더의 *.snapshot 으로 컬렉션을 되돌림

표준 라이브러리만 쓴다. Qdrant 주소는 QDRANT_URL (기본 http://qdrant:6333).
"""

import json
import os
import shutil
import sys
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

QDRANT = os.environ.get("QDRANT_URL", "http://qdrant:6333").rstrip("/")


def call(method: str, path: str, body=None, headers: dict | None = None):
    req = urllib.request.Request(QDRANT + path, data=body, method=method, headers=headers or {})
    return urllib.request.urlopen(req, timeout=3600)


def collections() -> list[str]:
    with call("GET", "/collections") as r:
        return [c["name"] for c in json.load(r)["result"]["collections"]]


def backup(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name in collections():
        q = urllib.parse.quote(name)
        with call("POST", f"/collections/{q}/snapshots?wait=true") as r:
            snap = json.load(r)["result"]["name"]
        target = out / f"{name}.snapshot"
        with call("GET", f"/collections/{q}/snapshots/{snap}") as r, target.open("wb") as f:
            shutil.copyfileobj(r, f)
        # 서버에 남은 스냅샷은 지운다 (디스크를 아끼고, 보관 기간을 이 백업 폴더로만 관리)
        call("DELETE", f"/collections/{q}/snapshots/{snap}?wait=true").close()
        print(f"qdrant: {name} → {target.name} ({target.stat().st_size:,} bytes)")


def restore(src: Path) -> None:
    files = sorted(src.glob("*.snapshot"))
    if not files:
        print("qdrant: 복원할 스냅샷이 없습니다")
        return
    for f in files:
        name = f.stem
        boundary = uuid.uuid4().hex
        head = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="snapshot"; '
            f'filename="{f.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'
        ).encode()
        tail = f"\r\n--{boundary}--\r\n".encode()

        def parts(f=f, head=head, tail=tail):
            # 큰 스냅샷도 메모리에 다 올리지 않고 조금씩 보낸다
            yield head
            with f.open("rb") as fh:
                while chunk := fh.read(1 << 20):
                    yield chunk
            yield tail

        q = urllib.parse.quote(name)
        path = f"/collections/{q}/snapshots/upload?priority=snapshot&wait=true"
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(head) + f.stat().st_size + len(tail)),
        }
        call("POST", path, parts(), headers).close()
        print(f"qdrant: {f.name} → 컬렉션 {name} 복원")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in {"backup", "restore"}:
        sys.exit(__doc__)
    (backup if sys.argv[1] == "backup" else restore)(Path(sys.argv[2]))
