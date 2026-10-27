"""登录扩展与系统管理(API-AUTH-014 + SYS-013 + API-SYS-001/010/012)。

取证(2026-10-27):
- 用户链:user/add(密码MD5)→登录→logout(GET,返回 code 400 但会话确实失效→
  旧 token 访问 500 loginOut)→batchSetStatus status=1 停用→登录 msgTip="user is black"→
  恢复→删除用户;
- 连续取号 defaultNumber 递增且不重号(SYS-010);
- log/list 裸调可用,行含 operation/content/userId(SYS-012)。
未实现:SYS-011 并发取号(会触发 DEF-EXP-06 同秒双发强制注销,与防攻击机制交叉,
改在性能批次观察)、SYS-014 菜单停用(影响系统菜单,风险大,保留手工)。
"""
import hashlib
import json
from datetime import datetime

import allure
import pytest

from api.auth_api import AuthApi
from common.asserts import assert_code_ok
from utils.dynamic import exp_id


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


@allure.feature("登录认证")
@pytest.mark.regression
class TestLogoutAndUserStatus:

    @allure.title("API-AUTH-014 logout 后旧 token 失效(实测返回 code400 但会话注销)")
    def test_014_logout(self, auth):
        s = auth["client"]
        uname = exp_id("EXPFu")
        assert_code_ok(s.post("/user/add", json_body={
            "username": uname, "loginName": uname, "password": _md5("123456"),
            "position": "", "email": "", "phonenum": "", "remark": ""}), "建测试用户")
        uid = None
        try:
            rows = s.get("/user/list", params={
                "search": json.dumps({"loginName": uname}, ensure_ascii=False),
                "currentPage": 1, "pageSize": 50}).json()["data"]["rows"]
            uid = rows[0]["id"]
            # 测试用户独立会话(不动共享 auth)
            import requests
            s2 = requests.Session()
            login_body = s2.post(auth["client"].base + "/user/login", json={
                "loginName": uname, "password": _md5("123456")}, timeout=15).json()
            utok = (login_body.get("data") or {}).get("token")
            assert utok, f"测试用户登录失败: {login_body}"
            import time
            time.sleep(2.1)
            lo = s2.get(auth["client"].base + "/user/logout",
                        headers={"X-Access-Token": utok}, timeout=15)
            assert lo.status_code == 200, f"logout HTTP 异常: {lo.status_code}"
            time.sleep(2.1)
            again = s2.get(auth["client"].base + "/user/getUserSession",
                           headers={"X-Access-Token": utok}, timeout=15)
            assert again.text.strip() == "loginOut", \
                f"logout 后旧 token 应失效(loginOut),实际: {again.text[:50]}"
        finally:
            if uid:
                s.delete("/user/deleteBatch", params={"ids": f"{uid},"})

    @allure.title("API-SYS-013 停用用户后登录被拒(user is black,与 AUTH-004 闭环)")
    def test_013_disable_user(self, auth):
        s = auth["client"]
        uname = exp_id("EXPFd")
        assert_code_ok(s.post("/user/add", json_body={
            "username": uname, "loginName": uname, "password": _md5("123456"),
            "position": "", "email": "", "phonenum": "", "remark": ""}), "建测试用户")
        uid = None
        try:
            rows = s.get("/user/list", params={
                "search": json.dumps({"loginName": uname}, ensure_ascii=False),
                "currentPage": 1, "pageSize": 50}).json()["data"]["rows"]
            uid = rows[0]["id"]
            assert_code_ok(s.post("/user/batchSetStatus",
                                  json_body={"status": "1", "ids": f"{uid},"}), "停用")
            import requests
            body = requests.Session().post(auth["client"].base + "/user/login", json={
                "loginName": uname, "password": _md5("123456")}, timeout=15).json()
            msg = (body.get("data") or {}).get("msgTip")
            assert msg == "user is black", f"停用后登录应 user is black,实际: {msg}"
        finally:
            if uid:
                s.post("/user/batchSetStatus", json_body={"status": "0", "ids": f"{uid},"})
                s.delete("/user/deleteBatch", params={"ids": f"{uid},"})


@allure.feature("系统管理")
@pytest.mark.regression
class TestSystemMore:

    @allure.title("API-SYS-001 角色新增/查询/删除")
    def test_001_role_crud(self, auth):
        s = auth["client"]
        role = exp_id("EXPFrole")
        assert_code_ok(s.post("/role/add", json_body={
            "name": role, "type": 0, "priceLimit": "", "remark": ""}), "新增角色")
        rows = s.get("/role/list", params={
            "search": json.dumps({"name": role}, ensure_ascii=False),
            "currentPage": 1, "pageSize": 50}).json()["data"]["rows"]
        hit = [r for r in rows if r["name"] == role]
        assert hit, f"角色列表应含新增角色: {rows[:3]}"
        assert_code_ok(s.delete("/role/deleteBatch", params={"ids": f"{hit[0]['id']},"}), "删除角色")
        rows2 = s.get("/role/list", params={
            "search": json.dumps({"name": role}, ensure_ascii=False),
            "currentPage": 1, "pageSize": 50}).json()["data"]["rows"]
        assert not [r for r in rows2 if r["name"] == role], "删除后角色不应可见"

    @allure.title("API-SYS-010 单据编号连续取号:递增且不重号(db_check)")
    @pytest.mark.db_check
    def test_010_sequence(self, auth):
        nums = [AuthApi(auth["client"]).c.get("/sequence/buildNumber").json()["data"]["defaultNumber"]
                for _ in range(3)]
        values = [int(n) for n in nums]
        assert values[0] < values[1] < values[2], f"取号应递增: {nums}"
        assert len(set(nums)) == 3, f"取号不应重号: {nums}"

    @allure.title("API-SYS-012 敏感操作日志留痕:新增供应商后 log 可查(db_check)")
    @pytest.mark.db_check
    def test_012_log_trace(self, auth, db):
        s = auth["client"]
        from api.supplier_api import SupplierApi
        name = exp_id("EXPFlog")
        assert_code_ok(SupplierApi(s).add(name, "供应商"), "触发留痕的操作")
        try:
            body = s.get("/log/list", params={"currentPage": 1, "pageSize": 50}).json()
            data = body.get("data") or {}
            assert isinstance(data.get("total"), int), f"log/list 结构异常: {str(body)[:120]}"
            contents = json.dumps(data.get("rows") or [], ensure_ascii=False)
            assert name in contents, f"日志应含操作对象 {name}(留痕断言)"
        finally:
            api = SupplierApi(s)
            for sid in api.find_ids_by_name(name):
                api.delete_batch([sid])
