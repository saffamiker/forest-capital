"""
tests/test_magic_link_email.py — pins the May 27 2026 rebrand of
the magic-link email template (Analytics Desk + McColl + Queens).

Builder is pure: it does not touch the email provider. send_magic_link()'s
production path (Resend as of May 25 2026) consumes the builder's output
verbatim, so a builder regression here would land in the inbox.
"""
from __future__ import annotations

from auth import (
    MAGIC_LINK_EXPIRY_MINUTES,
    MAGIC_LINK_SUBJECT,
    build_magic_link_email,
)


_MAGIC_URL = "https://analyticsdesk.app/auth/verify?token=abc.def.ghi"

# Production platform URL is passed explicitly to the builder rather
# than read from the imported PLATFORM_URL constant, which falls back
# to FRONTEND_URL (http://localhost:5173) in dev. Pinning the prod
# URL via kwarg keeps these tests deterministic regardless of the
# environment the suite runs under.
_PROD_PLATFORM = "https://analyticsdesk.app"


def _build_prod() -> tuple[str, str]:
    """Build the email under the production platform URL — every
    test that asserts the analyticsdesk.app domain appears in the
    template should use this rather than the bare builder call."""
    return build_magic_link_email(_MAGIC_URL, platform_url=_PROD_PLATFORM)


class TestSubject:
    def test_subject_matches_rebrand_spec(self) -> None:
        # User spec May 27 2026: subject reads "Your Analytics Desk
        # login link" — NOT "Forest Capital". A typo / wording drift
        # here would land in every team member's inbox.
        subject, _ = build_magic_link_email(_MAGIC_URL)
        assert subject == "Your Analytics Desk login link"

    def test_subject_carries_no_forest_capital(self) -> None:
        # Belt-and-braces — the rebrand removed the "Forest Capital"
        # wordmark from every user-facing surface. Pin that the
        # subject never re-introduces it.
        subject, _ = build_magic_link_email(_MAGIC_URL)
        assert "Forest Capital" not in subject

    def test_module_constant_is_the_subject(self) -> None:
        # The subject is exported as MAGIC_LINK_SUBJECT so the email
        # routing layer (and any future audit log) can reference the
        # exact wording without re-stringifying.
        subject, _ = build_magic_link_email(_MAGIC_URL)
        assert subject == MAGIC_LINK_SUBJECT


class TestHeaderBranding:
    def test_html_embeds_mccoll_logo_via_hosted_url(self) -> None:
        # User spec: NO CID attachments — they render inconsistently
        # across email clients (Outlook in particular shows broken-
        # image icons). Embed via hosted URLs only.
        _, html = _build_prod()
        assert "https://analyticsdesk.app/assets/logos/mccoll.jpeg" in html
        # Belt-and-braces: no CID scheme anywhere in the body.
        assert "cid:" not in html

    def test_html_embeds_queens_logo_via_hosted_url(self) -> None:
        _, html = _build_prod()
        assert "https://analyticsdesk.app/assets/logos/queens.png" in html

    def test_logos_carry_explicit_dimensions(self) -> None:
        # Several email clients (Outlook desktop, Yahoo) ignore CSS
        # width on <img>. Explicit width / height HTML attributes are
        # the email-safe way to bound the rendered size.
        _, html = build_magic_link_email(_MAGIC_URL)
        # Two logos × one width attribute each = at least 2 hits.
        assert html.count('width="120"') >= 2

    def test_logos_carry_alt_text(self) -> None:
        # An email client that fails to load the logo (CSP-strict
        # inbox, no-image preview) should still show what it is.
        _, html = build_magic_link_email(_MAGIC_URL)
        assert 'alt="McColl School of Business"' in html
        assert 'alt="Queens University of Charlotte"' in html

    def test_html_carries_headline(self) -> None:
        _, html = build_magic_link_email(_MAGIC_URL)
        assert "Portfolio Intelligence System" in html

    def test_html_carries_institutional_subtext(self) -> None:
        _, html = build_magic_link_email(_MAGIC_URL)
        assert ("McColl School of Business · "
                "Queens University of Charlotte") in html

    def test_html_carries_no_forest_capital_wordmark(self) -> None:
        # The rebrand: every user-facing surface drops the
        # "Forest Capital" wordmark in favour of the McColl /
        # Queens institutional pair.
        _, html = build_magic_link_email(_MAGIC_URL)
        assert "Forest Capital" not in html


class TestBodyAndButton:
    def test_html_preserves_existing_body_copy(self) -> None:
        # User spec: keep this line verbatim — it is clear and
        # correct. Pinning it prevents a future template tweak
        # from drifting the wording.
        _, html = build_magic_link_email(_MAGIC_URL)
        assert (
            f"Click below to log in. This link expires in "
            f"{MAGIC_LINK_EXPIRY_MINUTES} minutes."
        ) in html

    def test_button_href_is_the_magic_url(self) -> None:
        _, html = build_magic_link_email(_MAGIC_URL)
        # The href must be the EXACT magic_url passed in — no
        # wrapping, no tracking-pixel rewrite.
        assert f'href="{_MAGIC_URL}"' in html

    def test_button_label(self) -> None:
        _, html = build_magic_link_email(_MAGIC_URL)
        assert ">Log In<" in html

    def test_safe_ignore_line_present(self) -> None:
        _, html = build_magic_link_email(_MAGIC_URL)
        assert ("If you did not request this link, you can safely "
                "ignore this email.") in html

    def test_expiry_minutes_threaded(self) -> None:
        # The builder accepts a custom expiry minutes value so a
        # config change in MAGIC_LINK_EXPIRY_MINUTES is reflected
        # in the email without re-running this builder under the
        # default.
        _, html = build_magic_link_email(_MAGIC_URL, expiry_minutes=45)
        assert "45 minutes" in html


class TestFooter:
    def test_footer_carries_msfa_label(self) -> None:
        _, html = build_magic_link_email(_MAGIC_URL)
        assert "MSFA FNA 670" in html

    def test_footer_links_to_platform(self) -> None:
        _, html = _build_prod()
        # The footer is "MSFA FNA 670 · analyticsdesk.app" with
        # the domain rendered as a clickable link.
        assert 'href="https://analyticsdesk.app"' in html
        assert ">analyticsdesk.app<" in html


class TestEmailClientSafety:
    def test_no_style_blocks(self) -> None:
        # Gmail strips <style> blocks. Every style must be inline
        # on the element it applies to.
        _, html = build_magic_link_email(_MAGIC_URL)
        assert "<style" not in html
        assert "</style>" not in html

    def test_no_external_stylesheet(self) -> None:
        _, html = build_magic_link_email(_MAGIC_URL)
        assert '<link rel="stylesheet"' not in html

    def test_no_javascript(self) -> None:
        # Email clients strip <script>; including any is a red flag
        # and might trigger spam classifiers.
        _, html = build_magic_link_email(_MAGIC_URL)
        assert "<script" not in html
        assert "javascript:" not in html


class TestStagingOverride:
    def test_platform_url_kwarg_routes_logo_paths(self) -> None:
        # A staging deploy that overrides PLATFORM_URL needs the
        # logos to come from the same domain so a strict CSP / SPF-
        # aware inbox doesn't flag cross-origin asset loads.
        _, html = build_magic_link_email(
            _MAGIC_URL,
            platform_url="https://staging.analyticsdesk.app",
        )
        assert ("https://staging.analyticsdesk.app/assets/logos/"
                "mccoll.jpeg") in html
        assert ("https://staging.analyticsdesk.app/assets/logos/"
                "queens.png") in html
        # The footer link follows the override too.
        assert 'href="https://staging.analyticsdesk.app"' in html
