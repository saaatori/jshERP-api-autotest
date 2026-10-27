"""分层断言助手:结构(HTTP/JSON)→ 业务字段(code/msgTip/业务值)→ DB 由用例直查。

REQ-API-004:业务失败时 HTTP 与 code 均可能仍为 200,成败判定必须落到具体业务字段。
"""
import json


def get_json(resp):
    return resp.json()


def assert_code_ok(resp, msg: str = ""):
    body = resp.json()
    assert body.get("code") == 200, (
        f"{msg} 预期 code=200,实际: {json.dumps(body, ensure_ascii=False)[:300]}")


def assert_login_out(resp):
    assert HttpClient_is_login_out(resp), \
        f"预期响应体 loginOut(鉴权失效),实际: {resp.text[:100]}"


def HttpClient_is_login_out(resp):
    from common.client import HttpClient
    return HttpClient.is_login_out(resp)


def assert_business_fail(resp, *keywords):
    """业务失败断言:HTTP 200 且(code != 200 或报文含任一关键字)。"""
    body = resp.json()
    text = json.dumps(body, ensure_ascii=False)
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {text[:200]}"
    if keywords and any(k in text for k in keywords):
        return
    assert body.get("code") != 200, f"预期业务失败,实际成功: {text[:300]}"
