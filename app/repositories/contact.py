from app.models.contact import ContactMessage
from app.repositories.base import BaseRepository


class ContactRepository(BaseRepository[ContactMessage]):
	model = ContactMessage
