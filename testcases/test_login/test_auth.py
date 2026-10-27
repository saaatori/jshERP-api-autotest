"""登录与鉴权:API-AUTH-001/020 正向 + 登录参数异常矩阵 + 鉴权反例。

- 参数异常矩阵(002/003/005~009)由 data/login_negative.yaml 驱动,parametrize 展开。
- 反例用例一律不成功登录 jsh——同账号登录互踢,会破坏 conftest 里的共享会话。
"""
import re

import allure
import pytest

from api.auth_api import AuthApi
from common.asserts import assert_login_out
from common.dataloader import load_cases

TOKEN_RE = re.compile(r"^[0-9a-f]{32}_\d+$")


@allure.feature("登录认证")
@pytest.mark.smoke
class TestAuthSession:

    @allure.title("API-AUTH-001 正确账号密码登录成功,token 格式 UUID_租户ID")
    def test_001_login_success(self, auth):
        data = auth["login_resp"].json()["data"]
        assert data["msgTip"] == "user can login"
        assert TOKEN_RE.match(auth["token"]), f"token 格式不符: {auth['token']}"
        assert data["user"]["password"] is None, "登录响应未脱敏 password(NFR-SEC-003)"

    @allure.title("API-AUTH-020 getUserSession 返回会话信息")
    def test_020_get_user_session(self, auth):
        body = AuthApi(auth["client"]).get_user_session().json()
        assert body["code"] == 200
        assert body["data"]["user"]["id"] == auth["user_id"]


@allure.feature("登录认证")
@pytest.mark.regression
class TestAuthLoginMatrix:

    @allure.title("登录参数异常矩阵(数据驱动)")
    @pytest.mark.parametrize("case", load_cases("login_negative"),
                             ids=lambda c: c["case_id"])
    def test_login_negative(self, client, case):
        """成败判定落 msgTip(REQ-API-004);期望值按 2026-10-27 实测钉死。"""
        api = AuthApi(client)
        if case.get("login_name_char"):
            name = case["login_name_char"] * case["login_name_len"]
        else:
            name = case["login_name"]
        if case["password_mode"] == "md5":
            body = api.login(name, case["password"]).json()
        else:
            body = api.login_raw(name, case["password"]).json()
        assert body["code"] == 200, f"{case['case_id']} HTTP层异常: {body}"
        msg = body["data"]["msgTip"]
        assert msg == case["expect_msg"], \
            f"{case['case_id']}({case['title']}): 期望 {case['expect_msg']},实际 {msg}"


@allure.feature("登录认证")
@pytest.mark.regression
class TestAuthNegative:

    @allure.title("API-AUTH-018 连续失败5次均为密码错误提示(无锁定机制,NFR-SEC-005 风险)")
    def test_018_no_lockout_after_5_failures(self, client):
        # 不做"正确密码仍可登录"步骤:同账号登录互踢,会破坏共享会话
        api = AuthApi(client)
        for i in range(5):
            body = api.login("jsh", f"bad-pwd-{i}").json()
            assert body["data"]["msgTip"] == "user password error", \
                f"第{i + 1}次失败提示异常,疑似存在锁定机制: {body}"

    @allure.title("API-AUTH-010 无 token 访问 getUserList:响应体纯文本 loginOut")
    def test_010_no_token_get_user_list(self, client):
        r = client.get("/user/getUserList",
                       params={"search": "{}", "currentPage": 1, "pageSize": 10})
        assert_login_out(r)  # 实测 HTTP 500(与文档记录 200 不一致):状态码不统一,只断响应体

    @allure.title("API-AUTH-011 无 token 访问 role/allList:响应体 loginOut")
    def test_011_no_token_role_all_list(self, client):
        r = client.get("/role/allList")
        assert_login_out(r)  # 实测 HTTP 500,状态码不统一,断言以响应体为准

    @allure.title("API-AUTH-012 伪造 token 被拒")
    def test_012_fake_token(self, client):
        client.token = "fake-token_63"
        try:
            r = client.get("/user/getUserList",
                           params={"search": "{}", "currentPage": 1, "pageSize": 10})
            assert_login_out(r)
        finally:
            client.token = None
