from app.services.email_service import _prepare_waitlist


def test_prepare_waitlist_uses_frontend_url_fallback(monkeypatch):
	class _Settings:
		FRONTEND_URL = "https://example.com/"

	monkeypatch.setattr("app.services.email_service.get_settings", lambda: _Settings())

	ctx = {}
	_prepare_waitlist(ctx)

	assert ctx["first_name"] == "there"
	assert ctx["baseUrl"] == "https://example.com"


def test_prepare_waitlist_prefers_explicit_base_url(monkeypatch):
	class _Settings:
		FRONTEND_URL = "https://example.com/"

	monkeypatch.setattr("app.services.email_service.get_settings", lambda: _Settings())

	ctx = {"base_url": "https://frontend.test/"}
	_prepare_waitlist(ctx)

	assert ctx["baseUrl"] == "https://frontend.test"