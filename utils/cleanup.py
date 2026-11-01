"""残留数据清理工具(测试基础设施)。

设计:
- 仅清理自动化造数命名的内容:业务单据按单号前缀(CGRK/XSCK/CGDD/XSDD/QTRK/LSCK/DBCK,
  本环境初始 0 单据,这些前缀必为自动化产物),资料按 EXPF 前缀。
- 单据先走业务接口(已审核先反审核);status=2 已完成/3 部分执行的单按系统规则不可删
  (8500011/8500013),此时用 SQL 逻辑删除兜底——清理工具允许写库,业务断言仍只读。
- conftest 的 residue_guard fixture 每轮开跑前调用一次,实现自愈;
  也可独立运行:python -m utils.cleanup
- tenant_id 由调用方传入(取自登录 token 后缀),不在代码中硬编码环境 ID。
"""
import logging
import re

log = logging.getLogger("jsherp")

BILL_PREFIXES = ("CGRK", "XSCK", "CGDD", "XSDD", "QTRK", "QTCK", "LSCK", "LSTK",
                 "DBCK", "CGTK", "XSTK", "ZZ", "CX")
FIN_PREFIXES = ("FKD", "SKD", "YSD", "ZCD", "ZZD", "SKQ")
AUTO_NAME_RE = re.compile(r"^(EXPF|E[0-9a-f]{6})")
# 演示库种子单位(个/箱等),保留不清理;换环境重建库后需按新库实际种子 id 调整
INITIAL_UNIT_IDS = (15, 19, 20)

SQL_BYID = {
    "depot_head": "UPDATE jsh_depot_head SET delete_flag=1 WHERE id=%s",
    "account_head": "UPDATE jsh_account_head SET delete_flag=1 WHERE id=%s",
    "material": "UPDATE jsh_material SET delete_flag=1 WHERE id=%s",
    "supplier": "UPDATE jsh_supplier SET delete_flag=1 WHERE id=%s",
    "depot": "UPDATE jsh_depot SET delete_flag=1 WHERE id=%s",
    "account": "UPDATE jsh_account SET delete_flag=1 WHERE id=%s",
    "unit": "UPDATE jsh_unit SET delete_flag=1 WHERE id=%s",
    "material_category": "UPDATE jsh_material_category SET delete_flag=1 WHERE id=%s",
    "role": "UPDATE jsh_role SET delete_flag=1 WHERE id=%s",
    "user": "UPDATE jsh_user SET delete_flag=1 WHERE id=%s",
    "user_business": "UPDATE jsh_user_business SET delete_flag=1 WHERE id=%s",
}


def _sql_flag(db, table, row_id, reason):
    db.execute(SQL_BYID[table], (row_id,))
    log.info("清理(SQL逻辑删) %s id=%s (%s)", table, row_id, reason)


def delete_materials_with_fallback(client, db, material_ids):
    """商品删除兜底:有过单据历史的商品接口删除常被 601 拦截(依赖检查含库存
    残留行/已逻辑删单据的明细,缺陷候选 #27,页面同样删不掉),SQL 逻辑删兜底。"""
    for mid in list(dict.fromkeys(material_ids)):
        try:
            body = client.delete("/material/deleteBatch",
                                 params={"ids": f"{mid},"}).json()
            if body.get("code") != 200:
                log.warning("teardown 商品 %s 接口删除失败(%s),SQL 兜底", mid, body.get("code"))
                db.execute("UPDATE jsh_material SET delete_flag=1 WHERE id=%s", (mid,))
        except Exception as e:  # noqa: BLE001
            log.warning("teardown 清理商品 %s 异常: %s", mid, e)


DELETE_API = {
    "depot": "/depot/deleteBatch",
    "supplier": "/supplier/deleteBatch",
    "account": "/account/deleteBatch",
    "material": "/material/deleteBatch",
}


def delete_with_fallback(client, db, entity, ids):
    """通用资料删除兜底:接口删除被拒(601 依赖检查计入已逻辑删引用/库存残留行,
    缺陷候选 #27,页面同样删不掉)时按 SQL_BYID 逻辑删兜底。"""
    for iid in list(dict.fromkeys(ids)):
        try:
            body = client.delete(DELETE_API[entity], params={"ids": f"{iid},"}).json()
            if body.get("code") != 200:
                log.warning("teardown %s %s 接口删除失败(%s),SQL 兜底", entity, iid, body.get("code"))
                db.execute(SQL_BYID[entity], (iid,))
        except Exception as e:  # noqa: BLE001
            log.warning("teardown 清理 %s %s 异常: %s", entity, iid, e)


def delete_depots_with_fallback(client, db, depot_ids):
    delete_with_fallback(client, db, "depot", depot_ids)


def delete_bills_with_fallback(client, db, bill_ids):
    """fixture teardown 通用单据清理:已审核(st=1)先反审核,再删除;
    删除被拒(已完成 st=2/部分执行 st=3 等系统规则,F6)时 SQL 逻辑删兜底,
    与 cleanup_residue 的兜底策略一致——保证"自建自清"不留残留(2026-10-31
    全量回归暴露 5 处 teardown 缺口后统一收敛到此助手)。"""
    for bid in list(dict.fromkeys(bill_ids)):
        try:
            row = db.query_one("SELECT status FROM jsh_depot_head WHERE id=%s", (bid,))
            if row and str(row["status"]) == "1":
                client.post("/depotHead/batchSetStatus",
                            json_body={"status": "0", "ids": f"{bid},"})
            body = client.delete("/depotHead/deleteBatch",
                                 params={"ids": f"{bid},"}).json()
            if body.get("code") != 200:
                log.warning("teardown 单据 %s 接口删除失败(%s),SQL 兜底", bid, body.get("code"))
                db.execute("UPDATE jsh_depot_head SET delete_flag=1 WHERE id=%s", (bid,))
        except Exception as e:  # noqa: BLE001
            log.warning("teardown 清理单据 %s 异常: %s", bid, e)


def cleanup_residue(client, db, tenant_id) -> dict:
    """扫描并清理残留,返回统计。失败项仅记录不抛异常(自愈尽力而为)。

    tenant_id:目标租户(登录 token 后缀);LIKE 模式一律作为 %s 参数传入,
    避免 pymysql 对含字面 % 的 SQL 做参数格式化时报错(类别扫描曾因此静默失效)。
    """
    stat = {"bills": 0, "fins": 0, "materials": 0, "suppliers": 0, "depots": 0, "accounts": 0,
        "units": 0, "categories": 0, "roles": 0, "users": 0, "orphan_ubs": 0}

    def rows(table, sql, args):
        try:
            return db.query(sql, args)
        except Exception as e:  # noqa: BLE001
            log.warning("清理扫描失败 %s: %s", table, e)
            return []

    # 1) 业务单据
    like = "number LIKE %s OR " * len(BILL_PREFIXES)
    bills = rows("depot_head",
                 f"SELECT id, status FROM jsh_depot_head WHERE tenant_id=%s AND delete_flag=0 AND ({like[:-4]})",
                 (tenant_id, *tuple(f"{p}%" for p in BILL_PREFIXES)))
    for r in bills:
        bid = r["id"]
        if str(r["status"]) == "1":
            client.post("/depotHead/batchSetStatus",
                        json_body={"status": "0", "ids": f"{bid},"})
        body = client.delete("/depotHead/deleteBatch", params={"ids": f"{bid},"}).json()
        if body.get("code") == 200:
            stat["bills"] += 1
        else:  # 已完成/部分执行:系统规则不可删,SQL 兜底
            _sql_flag(db, "depot_head", bid, f"接口拒绝 {body.get('code')}")
            stat["bills"] += 1

    # 2) 财务单据
    like = "bill_no LIKE %s OR " * len(FIN_PREFIXES)
    fins = rows("account_head",
                f"SELECT id, status FROM jsh_account_head WHERE tenant_id=%s AND delete_flag=0 AND ({like[:-4]})",
                (tenant_id, *tuple(f"{p}%" for p in FIN_PREFIXES)))
    for r in fins:
        fid = r["id"]
        if str(r["status"]) == "1":
            client.post("/accountHead/batchSetStatus",
                        json_body={"status": "0", "ids": f"{fid},"})
        body = client.delete("/accountHead/deleteBatch", params={"ids": f"{fid},"}).json()
        if body.get("code") == 200:
            stat["fins"] += 1
        else:
            _sql_flag(db, "account_head", fid, f"接口拒绝 {body.get('code')}")
            stat["fins"] += 1

    # 3) 基础资料(单据已清,接口删除一般可成功;601 依赖时 SQL 兜底)
    # 铁律:候选行必须"取到真实名称且名称匹配自动化特征";取不到名称一律跳过,宁漏勿误删
    # (2026-10-27 事故:SELECT 漏名称列导致空名分支误伤全部初始往来单位,已恢复)
    for table, name_col, sql, api in [
        ("material", "name", "SELECT id, name FROM jsh_material WHERE tenant_id=%s AND delete_flag=0", "/material/deleteBatch"),
        ("supplier", "supplier", "SELECT id, supplier FROM jsh_supplier WHERE tenant_id=%s AND delete_flag=0", "/supplier/deleteBatch"),
        ("depot", "name", "SELECT id, name FROM jsh_depot WHERE tenant_id=%s AND delete_flag=0", "/depot/deleteBatch"),
        ("account", "name", "SELECT id, name FROM jsh_account WHERE tenant_id=%s AND delete_flag=0", "/account/deleteBatch"),
    ]:
        candidates = []
        for r in rows(table, sql, (tenant_id,)):
            name = str(r.get(name_col) or "")
            if not name:  # 名称缺失(查询缺陷或数据异常)→ 跳过,绝不猜测
                log.warning("清理跳过 %s id=%s:名称列为空,人工确认", table, r["id"])
                continue
            if AUTO_NAME_RE.match(name):
                candidates.append(r["id"])
            elif table == "supplier" and not name.strip():
                candidates.append(r["id"])  # 空名往来=BAS-003 缺陷产物
        for rid in list(dict.fromkeys(candidates)):
            body = client.delete(api, params={"ids": f"{rid},"}).json()
            if body.get("code") == 200:
                stat[table + "s"] += 1
            else:
                _sql_flag(db, table, rid, f"接口拒绝 {body.get('code')}")
                stat[table + "s"] += 1

    # 4) 多单位(种子单位之外的全部;种子 id 见 INITIAL_UNIT_IDS)
    unit_skip = ",".join(str(i) for i in INITIAL_UNIT_IDS)
    for r0 in rows("unit", f"SELECT id, name FROM jsh_unit WHERE tenant_id=%s AND delete_flag=0 "
                           f"AND id NOT IN ({unit_skip})", (tenant_id,)):
        body = client.delete("/unit/deleteBatch", params={"ids": f"{r0['id']},"}).json()
        if body.get("code") == 200:
            stat["units"] += 1
        else:
            _sql_flag(db, "unit", r0["id"], f"接口拒绝 {body.get('code')}")
            stat["units"] += 1

    # 5) 商品类别(EXPF)
    for r0 in rows("category", "SELECT id FROM jsh_material_category WHERE tenant_id=%s "
                               "AND delete_flag=0 AND name LIKE %s", (tenant_id, "EXPF%")):
        body = client.delete("/materialCategory/deleteBatch", params={"ids": f"{r0['id']},"}).json()
        if body.get("code") == 200:
            stat["categories"] += 1
        else:
            _sql_flag(db, "material_category", r0["id"], f"接口拒绝 {body.get('code')}")
            stat["categories"] += 1

    # 6) 角色/用户(EXPF/E8c/E+6hex;user_business 孤儿授权——指向已删用户/角色的记录)
    for table, name_col, sql, api, key in [
        ("role", "name", "SELECT id, name FROM jsh_role WHERE tenant_id=%s AND delete_flag=0", "/role/deleteBatch", None),
        ("user", "login_name", "SELECT id, login_name FROM jsh_user WHERE tenant_id=%s AND delete_flag=0", "/user/deleteBatch", None),
    ]:
        for r0 in rows(table, sql, (tenant_id,)):
            name = str(r0.get(name_col) or "")
            if not AUTO_NAME_RE.match(name) and not name.startswith("E8c"):
                continue
            body = client.delete(api, params={"ids": f"{r0['id']},"}).json()
            if body.get("code") == 200:
                stat[table + "s"] += 1
            else:
                _sql_flag(db, table, r0["id"], f"接口拒绝 {body.get('code')}")
                stat[table + "s"] += 1
    orphans = rows("user_business",
                   "SELECT ub.id FROM jsh_user_business ub WHERE ub.tenant_id=%s AND ub.delete_flag=0 AND ("
                   "(ub.type IN ('UserRole','UserDepot','UserCustomer') AND NOT EXISTS "
                   "(SELECT 1 FROM jsh_user u WHERE u.id=ub.key_id AND u.delete_flag=0)) "
                   "OR (ub.type='RoleFunctions' AND NOT EXISTS "
                   "(SELECT 1 FROM jsh_role r WHERE r.id=ub.key_id AND r.delete_flag=0)))", (tenant_id,))
    orphan_ids = ",".join(f"{r0['id']}," for r0 in orphans)
    if orphan_ids:
        body = client.delete("/userBusiness/deleteBatch", params={"ids": orphan_ids}).json()
        if body.get("code") == 200:
            stat["orphan_ubs"] = len(orphans)
        else:
            for r0 in orphans:
                _sql_flag(db, "user_business", r0["id"], f"接口拒绝 {body.get('code')}")
            stat["orphan_ubs"] = len(orphans)
    return stat


if __name__ == "__main__":
    import sys
    sys.path.insert(0, __import__("pathlib").Path(__file__).resolve().parent.parent)
    from common.client import HttpClient
    from common.config import CFG
    from utils.db import DB
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    c = HttpClient(CFG["base_url"], CFG["timeout"], CFG["request_min_interval"])
    from api.auth_api import AuthApi
    resp = AuthApi(c).login(CFG["admin"]["login_name"], CFG["admin"]["password"]).json()
    assert resp["data"]["msgTip"] == "user can login", resp
    c.token = resp["data"]["token"]
    tenant_id = c.token.rsplit("_", 1)[-1]
    _db = DB(**CFG["db"])
    print("清理统计:", cleanup_residue(c, _db, tenant_id))
    c.session.close()
    _db.close()
