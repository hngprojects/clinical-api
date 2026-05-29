from __future__ import annotations


def validate_password_strength(value: str) -> str:
	errors: list[str] = []

	if len(value) < 8:
		errors.append("at least 8 characters")

	if not any(character.isupper() for character in value):
		errors.append("one uppercase letter")

	if not any(character.islower() for character in value):
		errors.append("one lowercase letter")

	if not any(character.isdigit() for character in value):
		errors.append("one number")

	if not any(character in "!@#$%^&*()_+-=[]{}|;':\",./<>?" for character in value):
		errors.append("one special character")

	if errors:
		raise ValueError("Password must contain " + ", ".join(errors) + ".")

	return value
