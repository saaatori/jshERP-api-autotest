"""系统权限(API-SYS-002/005/006 + DEF-EXP-10):功能权限/数据权限/接口层越权。

取证(2026-10-27,权限链全流程实测):
- 配置链路可用:role/add → userBusiness/add(RoleFunctions=UserDepot/UserRole/UserCustomer,
  value 格式 "[功能id][仓库id]")→ 用户登录即生效;
- **仓库数据权限生效**:受限用户 findUserDepot 仅返回授权仓库(checked=true);
- **功能权限接口层完全不拦截(DEF-EXP-10,重大安全缺陷)**:仅授商品模块的用户,
  调用户管理/角色管理/建供应商均 200——功能权限仅控制前端菜单显示,接口无鉴权,
  与用例集 SYS-003"前后端双重校验"预期矛盾,按实测断言(修复后翻转)。
"""
import hashlib
import json
import time

import allure
import pytest

from common.asserts import assert_code_ok
from utils.dynamic import exp_id


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


@pytest.fixture(scope="module")
def perm_env(auth, db):
    """权限链完整环境:角色(仅商品模块功能)→ 用户(绑角色+仅授权仓库1)→ 用户会话。"""
    s = {"client": auth["client"], "db": db, "role_ids": [], "user_ids": [], "ub_ids": []}
    role_name = exp_id("EXPFrole")
    assert_code_ok(s["client"].post("/role/add", json_body={
        "name": role_name, "type": "个人数据", "priceLimit": "", "remark": ""}), "建角色")
    s["role_id"] = db.query_one(
        "SELECT id FROM jsh_role WHERE name=%s AND tenant_id=%s AND delete_flag=0",
        (role_name, auth["tenant_id"]))["id"]
    # 仅授商品模块功能(21 商品管理 + 22/23/236/247 子项)
    fn_ids = "".join(f"[{r['id']}]" for r in db.query(
        "SELECT id FROM jsh_function WHERE delete_flag=0 AND id IN (21,22,23,236,247)"))
    body = s["client"].post("/userBusiness/add", json_body={
        "type": "RoleFunctions", "keyId": str(s["role_id"]),
        "value": fn_ids, "btnstr": ""}).json()
    assert_code_ok_wrap = body.get("code") == 200
    assert assert_code_ok_wrap, f"功能授权失败: {body}"
    # 建用户并绑定
    uname = exp_id("EXPFu")
    assert_code_ok(s["client"].post("/user/add", json_body={
        "username": uname, "loginName": uname, "password": _md5("123456"),
        "position": "", "email": "", "phonenum": "", "remark": ""}), "建用户")
    s["user_id"] = db.query_one(
        "SELECT id FROM jsh_user WHERE login_name=%s AND tenant_id=%s AND delete_flag=0",
        (uname, auth["tenant_id"]))["id"]
    for t, v in (("UserRole", f"[{s['role_id']}]"), ("UserDepot", "[14]"), ("UserCustomer", "")):
        body = s["client"].post("/userBusiness/add", json_body={
            "type": t, "keyId": str(s["user_id"]), "value": v, "btnstr": ""}).json()
        assert body.get("code") == 200, f"{t} 配置失败: {body}"
    # 用户登录(独立会话)
    import requests
    s["uname"] = uname
    s2 = requests.Session()
    login = s2.post(auth["client"].base + "/user/login", json={
        "loginName": uname, "password": _md5("123456")}, timeout=15).json()
    s["utok"] = (login.get("data") or {}).get("token")
    assert s["utok"], f"受限用户登录失败: {login}"
    yield s
    for uid in s["user_ids"] + [s["user_id"]]:
        s["client"].delete("/user/deleteBatch", params={"ids": f"{uid},"})
    ub_rows = db.query(
        "SELECT id FROM jsh_user_business WHERE delete_flag=0 AND "
        "((key_id=%s AND type IN ('UserRole','UserDepot','UserCustomer')) "
        "OR (key_id=%s AND type='RoleFunctions'))",
        (str(s["user_id"]), str(s["role_id"])))
    ub_ids = ",".join(f"{r['id']}," for r in ub_rows)
    if ub_ids:
        s["client"].delete("/userBusiness/deleteBatch", params={"ids": ub_ids})
    s["client"].delete("/role/deleteBatch", params={"ids": f"{s['role_id']},"})


@allure.feature("系统权限")
@pytest.mark.permission
@pytest.mark.usefixtures("env_check")
class TestPermission:

    @allure.title("API-SYS-002 角色功能权限配置链路生效,但接口层不拦截(DEF-EXP-10 回归)")
    def test_002_role_function_permission(self, perm_env):
        s = perm_env
        uh = {"X-Access-Token": s["utok"]}
        # 已授权模块:商品列表正常
        import requests
        time.sleep(2.1)
        resp_ok = requests.get(s["client"].base + "/material/list", headers=uh,
                               params={"currentPage": 1, "pageSize": 3}, timeout=15)
        assert resp_ok.status_code == 200 and resp_ok.json().get("code") == 200, \
            f"已授权模块应可访问: {resp_ok.text[:100]}"
        # 未授权模块:用户管理——缺陷现状仍 200(接口层不拦截)
        time.sleep(2.1)
        resp_bad = requests.get(s["client"].base + "/user/list", headers=uh,
                                params={"search": "{}", "currentPage": 1, "pageSize": 3},
                                timeout=15)
        body = resp_bad.json()
        assert body.get("code") == 200, \
            f"实测接口层不拦截(缺陷现状 200),若已加接口鉴权请翻转断言: {body}"

    @allure.title("API-SYS-005 仓库数据权限生效:授权仓库勾选、其余仓库不勾选(findUserDepot)")
    def test_005_user_depot_scope(self, perm_env):
        """findUserDepot 返回全部仓库树,以 checked 状态区分授权——数据权限的实际口径。"""
        s = perm_env
        uh = {"X-Access-Token": s["utok"]}
        import requests
        import re
        time.sleep(2.1)
        resp = requests.get(s["client"].base + "/depot/findUserDepot", headers=uh,
                            params={"UBType": "UserDepot", "UBKeyId": s["user_id"]},
                            timeout=15)
        # 结构实测:授权仓库带 "checked":true,未授权仓库无 checked 字段
        items = re.findall(
            r'\{"checked":(true|false),"attributes":"([^"]*)","id":(\d+),"title":"([^"]+)"'
            r'|\{"attributes":"([^"]*)","id":(\d+),"title":"([^"]+)"', resp.text)
        assert items, f"应返回仓库授权树: {resp.text[:150]}"
        checked, unchecked = set(), set()
        for flag, _a1, _i1, t1, _a2, _i2, t2 in items:
            (checked if flag == "true" else unchecked).add(t1 or t2)
        assert "仓库1" in checked, f"授权仓库1应勾选: checked={checked}"
        assert {"仓库2", "仓库3"} <= unchecked, \
            f"未授权仓库2/3应无勾选标记: checked={checked}, unchecked={unchecked}"

    @allure.title("API-SYS-006 受限用户直调写接口(未授权模块)不拦截——DEF-EXP-10 回归")
    def test_006_write_bypass(self, perm_env):
        s = perm_env
        uh = {"X-Access-Token": s["utok"]}
        import requests
        time.sleep(2.1)
        resp = requests.post(s["client"].base + "/role/add", headers=uh, json={
            "name": exp_id("EXPFbypass"), "type": "个人数据",
            "priceLimit": "", "remark": ""}, timeout=15)
        body = resp.json()
        assert body.get("code") == 200, \
            f"实测越权写不拦截(缺陷现状 200),若已加接口鉴权请翻转断言: {body}"
        # 清理越权创建的角色
        rows = s["db"].query(
            "SELECT id FROM jsh_role WHERE tenant_id=%s AND delete_flag=0 AND name LIKE %s",
            (s["client"].token.split("_")[-1], "EXPFbypass%"))
        for row in rows:
            s["client"].delete("/role/deleteBatch", params={"ids": f"{row['id']},"})
