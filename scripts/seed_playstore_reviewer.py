import asyncio
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.user import User, UserRole


async def seed_reviewer_account() -> None:
	settings = get_settings()
	email = (
		settings.TEST_REVIEWER_EMAILS[0]
		if settings.TEST_REVIEWER_EMAILS
		else "playstore.reviewer@clinsights.com"
	)
	password = "PlayStoreReviewer2026!"

	async with AsyncSessionLocal() as session:
		result = await session.execute(
			select(User).where(User.email == email, User.role == UserRole.PATIENT)
		)
		existing = result.scalar_one_or_none()

		if existing:
			existing.is_email_verified = True
			existing.is_active = True
			existing.password_hash = hash_password(password)
			print(f"Successfully updated reviewer account: {email}")
		else:
			user = User(
				email=email,
				first_name="PlayStore",
				last_name="Reviewer",
				role=UserRole.PATIENT,
				is_email_verified=True,
				is_active=True,
				password_hash=hash_password(password),
			)
			session.add(user)
			print(f"Successfully created reviewer account: {email}")

		await session.commit()


if __name__ == "__main__":
	asyncio.run(seed_reviewer_account())
