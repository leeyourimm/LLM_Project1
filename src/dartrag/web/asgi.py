"""배포용 ASGI 진입점: uvicorn dartrag.web.asgi:app

프록시(Caddy) 뒤에서는 --proxy-headers 를 켜야 요청 한도·로그인 제한이 실제 접속 주소로
동작한다. 지표(/metrics)를 프로세스 하나에서 세므로 --workers 는 1 로 둔다.
"""

import logging

from dartrag.config import get_settings
from dartrag.db import Repository
from dartrag.obs.errors import init_sentry
from dartrag.web.app import create_app
from dartrag.web.services import default_services


def build():
    settings = get_settings()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    init_sentry(settings, "api")
    repo = Repository.connect(settings.database_url)
    try:
        repo.migrate()
    finally:
        repo.conn.close()
    return create_app(default_services(settings))


app = build()
