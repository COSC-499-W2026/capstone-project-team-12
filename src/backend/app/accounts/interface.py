"""Public interface of app.accounts: the only module other components may import.

Planned (Interface card #10): get_current_user, require_class_role, can_manage_content and
is_ta_in_language. Agree on each signature with its users before adding it here.
"""

from pydantic import BaseModel

from app.accounts.register import register_account

class RegisterRequest(BaseModel):
    def __init__(self, email: str, username: str, password: str, access_code: str | None = None):
        self.email = email
        self.username = username
        self.password = password
        self.access_code = access_code
    
    email: str
    username: str
    password: str
    access_code: str | None = None

def student_register(request: RegisterRequest) -> None:
    register_account(request.email, request.username, request.password, request.access_code)