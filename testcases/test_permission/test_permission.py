"""系统权限(API-SYS-002/005/006/009/015 + DEF-EXP-10):功能权限/数据权限/接口层越权/部门/租户。

取证(2026-10-27,权限链全流程实测):
- 配置链路可用:role/add → userBusiness/add(RoleFunctions=UserDepot/UserRole/UserCustomer,
  value 格式 "[功能id][仓库id]")→ 用户登录即生效;
- **仓库数据权限生效**:受限用户 findUserDepot 仅返回授权仓库(checked=true);
- **功能权限接口层完全不拦截(DEF-EXP-10,重大安全缺陷)**:仅授商品模块的用户,
  调用户管理/角色管理/建供应商均 200——功能权限仅控制前端菜单显示,接口无鉴权,
  与用例集 SYS-003"前后端双重校验"预期矛盾,按实测断言(修复后翻转)。
2026-10-31 补齐 SYS-015(部门树形)/SYS-009(租户创建,缺陷回归,见 TestSysRepresentative)。
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


@allure.feature("系统权限")
@pytest.mark.regression
@pytest.mark.usefixtures("env_check")
class TestSysRepresentative:
    """系统管理 D3 模块代表用例(2026-10-31 补齐,方案A):部门此前零接口用例,
    租户创建实测为后端缺陷——取证链:tenant/add → registerUser → 权限绑定走
    UserBusinessMapper.insertSelective,其 SQL 为
    "INSERT INTO jsh_user_business (type,key_id,value,tenant_id,tenant_id) VALUES(?,?,?,?,63)"
    (tenant_id 列写两遍:占位符+硬编码63,UserBusinessMapper.xml 131 行动态列后带字面量),
    BadSqlGrammar → 事务回滚 → 恒 301"数据写入异常"。登录页"注册租户"入口
    (REQ-AUTH-006)同步失效。缺陷候选提交禅道;修复 mapper 后请翻转 SYS-009 断言。"""

    @allure.title("API-SYS-015 部门树形新增:子部门 parent_id 正确挂接+逆序删除(db_check)")
    @pytest.mark.db_check
    def test_015_org_tree(self, auth, db):
        s = {"client": auth["client"], "tenant_id": auth["tenant_id"], "db": db}
        no1, no2 = exp_id("EXPForg"), exp_id("EXPForg")
        assert_code_ok(s["client"].post("/organization/add", json_body={
            "orgNo": no1, "orgAbr": no1 + "A", "parentId": "", "sort": "", "remark": ""}), "父部门")
        p1 = s["db"].query_one(
            "SELECT id FROM jsh_organization WHERE org_no=%s AND tenant_id=%s AND delete_flag=0",
            (no1, s["tenant_id"]))
        assert p1, "父部门应落库"
        try:
            assert_code_ok(s["client"].post("/organization/add", json_body={
                "orgNo": no2, "orgAbr": no2 + "B", "parentId": p1["id"],
                "sort": "", "remark": ""}), "子部门")
            p2 = s["db"].query_one(
                "SELECT id, parent_id FROM jsh_organization WHERE org_no=%s AND delete_flag=0",
                (no2,))
            assert p2 and str(p2["parent_id"]) == str(p1["id"]), \
                f"子部门应挂接父部门: {p2}"
            # 删除保护:存在下级的部门不允许删除(11000006)
            guard = s["client"].delete("/organization/deleteBatch",
                                       params={"ids": f"{p1['id']},"}).json()
            assert guard.get("code") == 11000006, \
                f"存在下级的部门删除应被 11000006 拦截: {guard}"
            assert_code_ok(s["client"].delete("/organization/deleteBatch",
                                              params={"ids": f"{p2['id']},"}), "删子部门")
        finally:
            # organization 不在 residue_guard 扫描范围,断言失败时也须清理
            for no in (no2, no1):
                row = s["db"].query_one(
                    "SELECT id FROM jsh_organization WHERE org_no=%s AND delete_flag=0", (no,))
                if row:
                    s["client"].delete("/organization/deleteBatch",
                                       params={"ids": f"{row['id']},"})

    @allure.title("API-SYS-009 登录态新建租户恒301(部署包注入tenant_id=63与实体冲突,缺陷回归,db_check)")
    @pytest.mark.db_check
    def test_009_tenant_add_broken(self, auth, db):
        """2026-10-31 定界(经用户复核):登录态(带 X-Access-Token)调 /tenant/add 恒 301——
        对照日志实证:带会话时 jsh_user_business 插入被改写为 5 列(注入字面量 tenant_id=63,
        与实体自带 tenant_id 构成重复列);未登录时同一语句为 4 列干净插入。
        登录页"注册租户"走 POST /user/registerUser(未登录白名单),实测可用
        (创建租户 204/205 并验证新租户数据隔离)——受影响的仅是系统管理→租户管理
        页面的"新增"按钮(登录态)。断言登录态缺陷现状;部署包修复后请翻转。
        另:部署 jar 含该注入而公开源码 8f5dc08 无——源码对照需以容器实测为准。"""
        import hashlib
        import requests
        import time
        tname = exp_id("EXPFt")
        pwd = hashlib.md5(b"123456").hexdigest()
        resp = auth["client"].post("/tenant/add", json_body={
            "loginName": tname, "username": tname, "password": pwd,
            "userNumLimit": 2, "type": "", "enabled": True, "remark": "AUTO"}).json()
        assert resp.get("code") == 301, f"登录态缺陷现状应恒 301,若已修复请翻转: {resp}"
        assert not db.query_one(
            "SELECT id FROM jsh_user WHERE login_name=%s", (tname,)), \
            "事务应回滚,无用户残留"
        time.sleep(2.1)
        lg = requests.post(auth["client"].base + "/user/login",
                           json={"loginName": tname, "password": pwd}, timeout=15).json()
        assert (lg.get("data") or {}).get("msgTip") == "user is not exist", \
            f"该登录名不应存在: {lg}"

    @allure.title("API-SYS-008 注册租户数据隔离:新租户看不到主租户资料(db_check)")
    @pytest.mark.db_check
    def test_008_tenant_isolation(self, auth, db):
        """走未登录 /user/registerUser(实测可用,见 SYS-009 定界):新租户登录后
        供应商下拉应为空(与主租户数据隔离)。清理走 SQL 兜底(租户删除仅 admin 可用)。"""
        import hashlib
        import requests
        import time
        tname = exp_id("EXPFt")
        pwd = hashlib.md5(b"123456").hexdigest()
        r = requests.post(auth["client"].base + "/user/registerUser", json={
            "loginName": tname, "password": pwd}, timeout=15).json()
        assert r.get("code") == 200, f"注册租户应成功: {r}"
        u = db.query_one("SELECT id, tenant_id FROM jsh_user WHERE login_name=%s", (tname,))
        assert u and str(u["tenant_id"]) == str(u["id"]), f"新租户 tenant_id 应=自身id: {u}"
        try:
            time.sleep(2.1)
            lg = requests.post(auth["client"].base + "/user/login", json={
                "loginName": tname, "password": pwd}, timeout=15).json()
            ntok = (lg.get("data") or {}).get("token")
            assert ntok and ntok.rsplit("_", 1)[-1] == str(u["id"]), f"token 后缀应=新租户 id: {lg}"
            time.sleep(2.1)
            iso = requests.post(auth["client"].base + "/supplier/findBySelect_sup", json={
                "limit": 1}, headers={"X-Access-Token": ntok}, timeout=15).json()
            assert iso == [], f"新租户供应商下拉应为空(数据隔离),实际: {iso}"
        finally:
            db.execute("UPDATE jsh_user SET delete_flag=1 WHERE id=%s", (u["id"],))
            db.execute("UPDATE jsh_user_business SET delete_flag=1 WHERE key_id=%s AND type='UserRole'",
                       (str(u["id"]),))
            db.execute("UPDATE jsh_tenant SET delete_flag=1 WHERE tenant_id=%s", (u["tenant_id"],))

    @allure.title("API-SYS-003 按钮权限 btnStr 配置写入并回读一致(db_check)")
    @pytest.mark.db_check
    def test_003_btn_str(self, auth, db):
        """按钮权限口径:role/add → userBusiness/add(RoleFunctions) → updateBtnStr 写按钮位
        → DB jsh_user_business.btn_str 回读一致(页面按钮可见性的后端落库口径)。"""
        role_name = exp_id("EXPFrole")
        assert_code_ok(auth["client"].post("/role/add", json_body={
            "name": role_name, "type": "个人数据", "priceLimit": "", "remark": ""}), "建角色")
        role_id = db.query_one(
            "SELECT id FROM jsh_role WHERE name=%s AND tenant_id=%s AND delete_flag=0",
            (role_name, auth["tenant_id"]))["id"]
        try:
            fn_ids = "".join(f"[{r['id']}]" for r in db.query(
                "SELECT id FROM jsh_function WHERE delete_flag=0 AND id IN (21,22,23)"))
            assert_code_ok(auth["client"].post("/userBusiness/add", json_body={
                "type": "RoleFunctions", "keyId": str(role_id), "value": fn_ids, "btnstr": ""}), "功能授权")
            btn_str = "[21][22]"
            body = auth["client"].post("/userBusiness/updateBtnStr", json_body={
                "roleId": str(role_id), "btnStr": btn_str}).json()
            assert body.get("code") == 200, f"btnStr 写入失败: {body}"
            row = db.query_one(
                "SELECT btn_str FROM jsh_user_business WHERE key_id=%s AND type='RoleFunctions' AND delete_flag=0",
                (str(role_id),))
            assert row and btn_str in (row["btn_str"] or ""), f"btn_str 应回读一致: {row}"
        finally:
            ub = db.query_one(
                "SELECT id FROM jsh_user_business WHERE key_id=%s AND type='RoleFunctions' AND delete_flag=0",
                (str(role_id),))
            if ub:
                auth["client"].delete("/userBusiness/deleteBatch", params={"ids": f"{ub['id']},"})
            auth["client"].delete("/role/deleteBatch", params={"ids": f"{role_id},"})

