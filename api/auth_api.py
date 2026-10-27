import hashlib


class AuthApi:
    """登录鉴权:/user/login(密码前端 MD5 后提交,REQ-API-001)。"""

    def __init__(self, client):
        self.c = client

    @staticmethod
    def md5(text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def login(self, login_name: str, password_plain: str):
        return self.c.post("/user/login", json_body={
            "loginName": login_name, "password": self.md5(password_plain)})

    def login_raw(self, login_name: str, password: str):
        """原样提交(明文/超长/注入样例等参数异常用例)。"""
        return self.c.post("/user/login", json_body={
            "loginName": login_name, "password": password})

    def get_user_session(self):
        return self.c.get("/user/getUserSession")

    def get_user_list(self, search: str = "{}", page: int = 1, size: int = 10):
        return self.c.get("/user/getUserList",
                          params={"search": search, "currentPage": page, "pageSize": size})
