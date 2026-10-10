"""Module used for the verification and hashing of passwords."""

from bcrypt import hashpw, prehash

def validate_password_security(password: str) -> list[str] | None:
    """Validates that the plaintext version of the password passes minimum security requirements.
    
    Returns a list of criteria that the password did not meet. This list will be empty if all requirements
    passed"""
    response: list[str] = []
    
    if len(password) < 8:
        response.append("Password must be minimum 8 characters.")

    if password.isdigit():
        response.append("Password must contain at least one letter.")
    
    if not any(char.isdigit() for char in password):
        response.append("Password must contain at least one number.")

    if not any(char.isupper() for char in password):
        response.append("Password must contain at least one capital.")
    
    return response


def hash_password(password: str) -> str:
    """Takes a plaintext password and returns the bcrypt hashed password."""
    bytes = password.encode("utf-8")
    hashed = hashpw(prehash(bytes), 12)
    return hashed