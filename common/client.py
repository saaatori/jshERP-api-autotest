"""统一 HTTP 客户端:X-Access-Token 注入、请求限速(DEF-EXP-06)、请求摘要日志。"""
import json
import time

import requests

from utils.logger import get_logger


def _brief(obj, limit=300):
    if obj is None:
        return ""
    if isinstance(obj, dict) and "password" in obj:
        obj = {**obj, "password": "***"}
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(obj)
    return s[:limit]


def _brief_text(resp, limit=300):
    t = resp.text.strip()
    if t == "loginOut":
        return "loginOut(鉴权失效)"
    return t[:limit].replace("\n", " ")


class HttpClient:
    def __init__(self, base_url, timeout=15, min_interval=2.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.min_interval = min_interval
        self.token = None
        self._last = 0.0
        self.session = requests.Session()
        self.log = get_logger()

    def request(self, method, path, params=None, json_body=None):
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        headers = {"X-Access-Token": self.token} if self.token else {}
        self.log.info(">> %s %s %s", method, path, _brief(json_body))
        resp = self.session.request(method, self.base + path, params=params,
                                    json=json_body, headers=headers, timeout=self.timeout)
        self._last = time.monotonic()
        self.log.info("<< %s %s %s", resp.status_code, path, _brief_text(resp))
        return resp

    def get(self, path, params=None):
        return self.request("GET", path, params=params)

    def post(self, path, json_body=None):
        return self.request("POST", path, json_body=json_body)

    def put(self, path, json_body=None):
        return self.request("PUT", path, json_body=json_body)

    def delete(self, path, params=None):
        return self.request("DELETE", path, params=params)

    @staticmethod
    def is_login_out(resp) -> bool:
        """鉴权失效特征:响应体为纯文本 loginOut(非 JSON),HTTP 状态码不统一(200/500)。"""
        return resp.text.strip() == "loginOut"
