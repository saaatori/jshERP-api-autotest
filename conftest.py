"""全局 fixture:配置、DB、裸客户端、唯一登录会话(auth)、环境开关快照。

约定:
- 同账号登录互踢,全程共用 auth 会话;鉴权/登录反例一律不成功登录 jsh,只用裸客户端。
- 数据库仅作断言读取;造数/清理一律走接口。
"""
import pytest

from api.auth_api import AuthApi
from common.client import HttpClient
from common.config import CFG
from utils.db import DB


@pytest.fixture(scope="session")
def cfg():
    return CFG


@pytest.fixture(scope="session")
def db(cfg):
    _db = DB(**cfg["db"])
    yield _db
    _db.close()


@pytest.fixture(scope="session")
def client(cfg):
    """裸客户端(无 token):鉴权反例与登录失败用例专用。"""
    c = HttpClient(cfg["base_url"], cfg["timeout"], cfg["request_min_interval"])
    yield c
    c.session.close()


@pytest.fixture(scope="session")
def auth(cfg):
    """管理员会话(全程唯一):登录一次并注入 token;同账号重复登录会互踢,禁止再造会话。"""
    c = HttpClient(cfg["base_url"], cfg["timeout"], cfg["request_min_interval"])
    resp = AuthApi(c).login(cfg["admin"]["login_name"], cfg["admin"]["password"])
    body = resp.json()
    assert body.get("code") == 200, f"登录接口异常: {body}"
    data = body.get("data", {})
    assert data.get("msgTip") == "user can login", f"登录失败: {data}"
    token = data["token"]
    c.token = token
    meta = {"client": c, "token": token, "login_resp": resp,
            "tenant_id": token.split("_")[-1], "user_id": data["user"]["id"]}
    yield meta
    c.session.close()


@pytest.fixture(scope="session", autouse=True)
def residue_guard(auth, db):
    """开跑前自动清理上一轮残留(自愈):按自动化命名前缀扫描,失败仅告警。

    必须保持 autouse——曾因无引用导致守卫从未执行,仓库授权列表(user_business)
    被每轮新建仓库无限追加而膨胀,最终触发仓库服务崩溃(DEF-EXP-09)。
    """
    from utils.cleanup import cleanup_residue
    stat = cleanup_residue(auth["client"], db)
    if any(stat.values()):
        auth["client"].log.warning("开跑前清理残留: %s", stat)
    return stat


@pytest.fixture(scope="session")
def env_check(db, auth, cfg):
    """环境准入:执行前校验开关快照与需求分析附录C一致(不符应失败提示,防环境漂移)。"""
    row = db.query_one(
        "SELECT platform_value FROM jsh_platform_config WHERE platform_key='checkcode_flag'")
    expect = str(cfg["switch_expect"]["checkcode_flag"])
    assert row and row["platform_value"] == expect, \
        f"checkcode_flag 快照不符(期望{expect}): {row}——自动化依赖验证码关闭环境"
    flags = db.query_one(
        "SELECT minus_stock_flag FROM jsh_system_config WHERE tenant_id=%s",
        (auth["tenant_id"],))
    expect_flag = str(cfg["switch_expect"]["minus_stock_flag"])
    assert flags and str(flags["minus_stock_flag"]) == expect_flag, \
        f"minus_stock_flag 快照不符(期望{expect_flag}): {flags}"
    return True
